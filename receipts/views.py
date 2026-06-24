import csv
import datetime
import json
from collections import defaultdict
from decimal import Decimal

from django.contrib import messages
from django.contrib.auth import authenticate, login, logout, get_user_model
from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.db.models import Sum
from django.db.models.functions import TruncMonth
from django.http import HttpResponse
from django.shortcuts import render, redirect, get_object_or_404
from django.utils import timezone
from django.utils.dateparse import parse_datetime

from .forms import RegisterForm, LoginForm, ProfileForm
from .models import Store, Category, Receipt, Item, UserProfile, PendingImport, Recipient, PENDING_IMPORT_RETRY_DELAYS_HOURS
from .services.categorize import suggest_category_id
from .services.dps import parse_qr_url, fetch_check, is_cabinet_check_url
from .services.pending import process_pending_import, process_user_due_imports
from .services.tax_check import parse_chk_response

User = get_user_model()


# ---------------------------------------------------------------------------
# Auth
# ---------------------------------------------------------------------------

def register(request):
    if request.user.is_authenticated:
        return redirect("dashboard")
    form = RegisterForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        user = form.save()
        login(request, user, backend="receipts.backends.UsernameOrEmailBackend")
        messages.success(request, "Ласкаво просимо!")
        return redirect("dashboard")
    return render(request, "receipts/auth/register.html", {"form": form})


def login_view(request):
    if request.user.is_authenticated:
        return redirect("dashboard")
    form = LoginForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        user = authenticate(
            request,
            username=form.cleaned_data["username"],
            password=form.cleaned_data["password"],
        )
        if user:
            login(request, user)
            return redirect(request.GET.get("next") or "dashboard")
        form.add_error(None, "Невірний email або пароль.")
    return render(request, "receipts/auth/login.html", {"form": form})


def logout_view(request):
    logout(request)
    return redirect("login")


@login_required
def profile(request):
    user_profile, _ = UserProfile.objects.get_or_create(user=request.user)
    form = ProfileForm(request.POST or None, request.FILES or None, instance=user_profile)
    if request.method == "POST" and form.is_valid():
        form.save()
        messages.success(request, "Профіль оновлено.")
        return redirect("profile")
    return render(request, "receipts/profile.html", {"form": form, "user_profile": user_profile})


# ---------------------------------------------------------------------------
# Dashboard
# ---------------------------------------------------------------------------

def dashboard(request):
    if not request.user.is_authenticated:
        return redirect("scan")
    recent = (Receipt.objects
              .filter(user=request.user)
              .select_related("store")
              .order_by("-datetime")[:10])

    # Use local time for month boundaries so that e.g. a receipt at 01:00 Kyiv
    # on June 1st is counted in June, not May (UTC midnight ≠ local midnight).
    local_now = timezone.localtime(timezone.now())
    this_month_start = local_now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    last_month_end = this_month_start - datetime.timedelta(seconds=1)
    last_month_start = last_month_end.replace(day=1, hour=0, minute=0, second=0, microsecond=0)

    base = Receipt.objects.filter(user=request.user)
    this_month_total = (base.filter(datetime__gte=this_month_start)
                        .aggregate(total=Sum("total"))["total"] or 0)
    last_month_total = (base.filter(datetime__gte=last_month_start, datetime__lte=last_month_end)
                        .aggregate(total=Sum("total"))["total"] or 0)

    top_categories = (Item.objects
                      .filter(receipt__user=request.user, receipt__datetime__gte=this_month_start)
                      .exclude(category=None)
                      .values("category__name")
                      .annotate(total=Sum("total"))
                      .order_by("-total")[:3])

    gift_this_month = (
        Item.objects
        .filter(
            receipt__user=request.user,
            receipt__datetime__gte=this_month_start,
            for_recipient__isnull=False,
        )
        .aggregate(total=Sum("total"))["total"] or 0
    )

    pending_count = PendingImport.objects.filter(
        user=request.user, status__in=[PendingImport.PENDING, PendingImport.FAILED]
    ).count()

    return render(request, "receipts/dashboard.html", {
        "recent": recent,
        "this_month_total": this_month_total,
        "last_month_total": last_month_total,
        "top_categories": top_categories,
        "pending_count": pending_count,
        "gift_this_month": gift_this_month,
        "this_month_from": this_month_start.date().isoformat(),
        "this_month_to": local_now.date().isoformat(),
        "last_month_from": last_month_start.date().isoformat(),
        "last_month_to": last_month_end.date().isoformat(),
    })


# ---------------------------------------------------------------------------
# Scan / parse / save
# ---------------------------------------------------------------------------

def scan(request):
    return render(request, "receipts/scan.html")


def receipts_parse(request):
    qr_url = request.POST.get("qr_url", "").strip()
    xml_b64 = request.POST.get("check_xml_b64", "").strip()
    source_qr_url = qr_url

    if qr_url and not is_cabinet_check_url(qr_url):
        return render(request, "receipts/_review_error.html", {
            "error": "Непідтримуваний тип QR-коду. Підтримуються лише чеки з cabinet.tax.gov.ua/cashregs/.",
            "hide_xml_fallback": True,
        })

    try:
        if xml_b64:
            data = {"checkXml": xml_b64}
        elif qr_url:
            p = parse_qr_url(qr_url)
            data = fetch_check(id=p["id"], fn=p["fn"], sm=p["sm"], api_date=p["api_date"])
        else:
            raise ValueError("Provide qr_url or check_xml_b64")

        receipt = parse_chk_response(data)
        store = Store.objects.filter(fiscal_rro=receipt.fiscal_rro).first()
        categories = Category.objects.order_by("name")

        items = []
        cat_totals: dict[str, float] = defaultdict(float)

        for it in receipt.items:
            cat_id = suggest_category_id(barcode=it.barcode, name=it.name) or ""
            items.append({
                "code": it.code, "barcode": it.barcode, "name": it.name,
                "qty": str(it.qty), "unit": it.unit,
                "unit_price": str(it.unit_price), "total": str(it.total),
                "suggested_category_id": cat_id,
            })
            if cat_id:
                cat_name = next((c.name for c in categories if c.pk == cat_id), "Інше")
                cat_totals[cat_name] += float(it.total)
            else:
                cat_totals["Без категорії"] += float(it.total)

        preview_chart = json.dumps({
            "labels": list(cat_totals.keys()),
            "values": [round(v, 2) for v in cat_totals.values()],
        })

        existing_receipt = None
        if request.user.is_authenticated and receipt.fiscal_rro and receipt.order_num:
            existing_receipt = (
                Receipt.objects
                .filter(user=request.user, fn=receipt.fiscal_rro, check_id=receipt.order_num)
                .select_related("store")
                .first()
            )

        recipients = list(Recipient.objects.values_list("name", flat=True))

        return render(request, "receipts/_review.html", {
            "receipt": receipt, "store": store,
            "categories": categories, "items": items,
            "preview_chart": preview_chart,
            "source_qr_url": source_qr_url,
            "existing_receipt": existing_receipt,
            "recipients": recipients,
        })
    except Exception as e:
        if qr_url and not xml_b64:
            return render(request, "receipts/_review_pending.html", {
                "error": str(e),
                "qr_url": qr_url,
            })
        return render(request, "receipts/_review_error.html", {"error": str(e)})


@login_required
@transaction.atomic
def receipts_save(request):
    if request.method != "POST":
        return redirect("scan")

    fiscal_rro = request.POST.get("fiscal_rro", "").strip()
    display_name = request.POST.get("display_name", "").strip()
    legal_name = request.POST.get("legal_name", "").strip()
    check_id = request.POST.get("check_id", "").strip()
    fn = request.POST.get("fn", "").strip()
    receipt_datetime_str = request.POST.get("receipt_datetime", "").strip()
    total_str = request.POST.get("receipt_total", "0").strip()
    source = request.POST.get("source", "qr").strip()
    qr_url = request.POST.get("qr_url", "").strip()
    item_count = int(request.POST.get("item_count", 0))

    if fn and check_id:
        existing = Receipt.objects.filter(user=request.user, fn=fn, check_id=check_id).first()
        if existing:
            messages.info(request, "Цей чек вже збережено раніше.")
            return redirect("receipt_detail", pk=existing.pk)

    if source == "manual":
        # Manual receipts have no real RRO; key the store by shop name so that
        # saving a receipt for "Shop B" never overwrites the store record for "Shop A".
        manual_key = f"manual:{display_name}"[:32]
        store, _ = Store.objects.get_or_create(
            fiscal_rro=manual_key,
            defaults={"display_name": display_name, "legal_name": ""},
        )
    else:
        store, _ = Store.objects.update_or_create(
            fiscal_rro=fiscal_rro,
            defaults={"legal_name": legal_name, "display_name": display_name},
        )

    receipt_dt = parse_datetime(receipt_datetime_str)
    if receipt_dt is None:
        receipt_dt = timezone.now()
    if timezone.is_naive(receipt_dt):
        receipt_dt = timezone.make_aware(receipt_dt)

    receipt_label = request.POST.get("for_recipient", "").strip()
    receipt_recipient = None
    if receipt_label:
        receipt_recipient, _ = Recipient.objects.get_or_create(name=receipt_label)

    receipt = Receipt.objects.create(
        user=request.user,
        store=store,
        check_id=check_id,
        fn=fn,
        datetime=receipt_dt,
        total=total_str or 0,
        source=source,
        qr_url=qr_url,
        for_recipient=receipt_recipient,
    )

    for i in range(item_count):
        name = request.POST.get(f"name_{i}", "").strip()
        if not name:
            continue
        barcode = request.POST.get(f"barcode_{i}", "").strip()
        code = request.POST.get(f"code_{i}", "").strip()
        qty = request.POST.get(f"qty_{i}", "0").strip() or "0"
        unit = request.POST.get(f"unit_{i}", "pcs").strip()
        unit_price = request.POST.get(f"price_{i}", "0").strip() or "0"
        total = request.POST.get(f"total_{i}", "0").strip() or "0"
        category_id_str = request.POST.get(f"category_id_{i}", "").strip()
        category_id = int(category_id_str) if category_id_str else None

        item_label = request.POST.get(f"item_for_recipient_{i}", "").strip()
        item_recipient = None
        if item_label:
            item_recipient, _ = Recipient.objects.get_or_create(name=item_label)

        Item.objects.create(
            receipt=receipt, code=code, barcode=barcode,
            name=name, qty=qty, unit=unit,
            unit_price=unit_price, total=total,
            category_id=category_id,
            for_recipient=item_recipient,
        )

    return redirect("receipt_detail", pk=receipt.pk)


@login_required
def receipt_detail(request, pk):
    receipt = get_object_or_404(
        Receipt.objects.select_related("store"),
        pk=pk, user=request.user,
    )
    items = receipt.items.select_related("category").all()
    return render(request, "receipts/receipt_detail.html", {"receipt": receipt, "items": items})


@login_required
def receipt_delete(request, pk):
    receipt = get_object_or_404(Receipt, pk=pk, user=request.user)
    if request.method == "POST":
        receipt.delete()
        return redirect("dashboard")
    return redirect("receipt_detail", pk=pk)


@login_required
def pending_import_save(request):
    if request.method != "POST":
        return redirect("scan")
    qr_url = request.POST.get("qr_url", "").strip()
    if not qr_url or not is_cabinet_check_url(qr_url):
        return redirect("scan")

    existing = PendingImport.objects.filter(
        user=request.user, qr_url=qr_url,
        status__in=[PendingImport.PENDING, PendingImport.PROCESSING],
    ).first()
    if existing:
        messages.info(request, "Це посилання вже додано до черги очікування.")
        return redirect("pending_imports")

    with transaction.atomic():
        pi = PendingImport.objects.create(user=request.user, qr_url=qr_url)
        from datetime import timedelta
        pi.next_retry_at = pi.created_at + timedelta(hours=PENDING_IMPORT_RETRY_DELAYS_HOURS[0])
        pi.save(update_fields=["next_retry_at"])

    messages.success(request, "Посилання збережено. Спроба завантаження відбудеться через 2 години.")
    return redirect("pending_imports")


@login_required
def pending_imports(request):
    process_user_due_imports(request.user)
    items = (
        PendingImport.objects
        .filter(user=request.user)
        .select_related("receipt__store")
        .order_by("-created_at")
    )
    return render(request, "receipts/pending_imports.html", {"pending_imports": items})


@login_required
def pending_import_retry(request, pk):
    if request.method != "POST":
        return redirect("pending_imports")
    pi = get_object_or_404(PendingImport, pk=pk, user=request.user)
    if pi.status in [PendingImport.PENDING, PendingImport.FAILED]:
        if process_pending_import(pi):
            messages.success(request, f"Чек успішно завантажено.")
        else:
            pi.refresh_from_db()
            messages.warning(request, f"Не вдалося завантажити: {pi.last_error}")
    return redirect("pending_imports")


@login_required
def pending_import_delete(request, pk):
    if request.method != "POST":
        return redirect("pending_imports")
    pi = get_object_or_404(PendingImport, pk=pk, user=request.user)
    pi.delete()
    messages.success(request, "Запис видалено.")
    return redirect("pending_imports")


@login_required
@transaction.atomic
def receipt_edit(request, pk):
    receipt = get_object_or_404(Receipt, pk=pk, user=request.user, source="manual")

    if request.method == "POST":
        display_name = request.POST.get("display_name", "").strip()
        receipt_datetime_str = request.POST.get("receipt_datetime", "").strip()
        total_str = request.POST.get("receipt_total", "0").strip()
        item_count = int(request.POST.get("item_count", 0))

        manual_key = f"manual:{display_name}"[:32]
        store, _ = Store.objects.get_or_create(
            fiscal_rro=manual_key,
            defaults={"display_name": display_name, "legal_name": ""},
        )

        receipt_dt = parse_datetime(receipt_datetime_str)
        if receipt_dt is None:
            receipt_dt = timezone.now()
        if timezone.is_naive(receipt_dt):
            receipt_dt = timezone.make_aware(receipt_dt)

        receipt_label = request.POST.get("for_recipient", "").strip()
        receipt_recipient = None
        if receipt_label:
            receipt_recipient, _ = Recipient.objects.get_or_create(name=receipt_label)

        receipt.store = store
        receipt.datetime = receipt_dt
        receipt.total = total_str or 0
        receipt.for_recipient = receipt_recipient
        receipt.save()

        receipt.items.all().delete()
        for i in range(item_count):
            name = request.POST.get(f"name_{i}", "").strip()
            if not name:
                continue
            qty = request.POST.get(f"qty_{i}", "0").strip() or "0"
            unit = request.POST.get(f"unit_{i}", "pcs").strip()
            unit_price = request.POST.get(f"price_{i}", "0").strip() or "0"
            total = request.POST.get(f"total_{i}", "0").strip() or "0"
            category_id_str = request.POST.get(f"category_id_{i}", "").strip()
            category_id = int(category_id_str) if category_id_str else None

            item_label = request.POST.get(f"item_for_recipient_{i}", "").strip()
            item_recipient = None
            if item_label:
                item_recipient, _ = Recipient.objects.get_or_create(name=item_label)

            Item.objects.create(
                receipt=receipt,
                name=name, qty=qty, unit=unit,
                unit_price=unit_price, total=total,
                category_id=category_id,
                for_recipient=item_recipient,
            )

        messages.success(request, "Чек оновлено.")
        return redirect("receipt_detail", pk=receipt.pk)

    categories = Category.objects.order_by("name")
    recipients = list(Recipient.objects.values_list("name", flat=True))
    initial_rows = [
        {
            "name": item.name,
            "qty": float(item.qty),
            "unit": item.unit,
            "price": float(item.unit_price),
            "categoryId": item.category_id or "",
        }
        for item in receipt.items.select_related("category").order_by("pk")
    ]
    initial_recipient = receipt.for_recipient.name if receipt.for_recipient else None

    return render(request, "receipts/receipt_edit.html", {
        "receipt": receipt,
        "categories": categories,
        "recipients": recipients,
        "initial_rows": initial_rows,
        "initial_recipient": initial_recipient,
    })


def _receipt_date_range(request):
    """Return (from_date, to_date, from_str, to_str) in local time for list/export views."""
    local_now = timezone.localtime(timezone.now())
    from_str = request.GET.get("from_date", local_now.replace(day=1).date().isoformat())
    to_str = request.GET.get("to_date", local_now.date().isoformat())
    try:
        from_date = datetime.date.fromisoformat(from_str)
        to_date = datetime.date.fromisoformat(to_str)
    except ValueError:
        from_date = local_now.replace(day=1).date()
        to_date = local_now.date()
        from_str, to_str = from_date.isoformat(), to_date.isoformat()
    return from_date, to_date, from_str, to_str


def _receipts_for_range(user, from_date, to_date):
    from_dt = timezone.make_aware(datetime.datetime.combine(from_date, datetime.time.min))
    to_dt = timezone.make_aware(datetime.datetime.combine(to_date, datetime.time.max))
    return (
        Receipt.objects
        .filter(user=user, datetime__gte=from_dt, datetime__lte=to_dt)
        .select_related("store")
        .order_by("-datetime")
    )


@login_required
def receipts_list(request):
    from_date, to_date, from_str, to_str = _receipt_date_range(request)
    receipts = _receipts_for_range(request.user, from_date, to_date)
    total = receipts.aggregate(s=Sum("total"))["s"] or 0
    return render(request, "receipts/receipts_list.html", {
        "receipts": receipts,
        "from_date": from_str,
        "to_date": to_str,
        "grand_total": total,
    })


@login_required
def receipts_export_csv(request):
    from_date, to_date, from_str, to_str = _receipt_date_range(request)
    receipts = _receipts_for_range(request.user, from_date, to_date)

    response = HttpResponse(content_type="text/csv; charset=utf-8")
    response["Content-Disposition"] = (
        f'attachment; filename="receipts_{from_str}_{to_str}.csv"'
    )
    response.write("﻿")  # UTF-8 BOM so Excel opens it correctly

    writer = csv.writer(response)
    writer.writerow(["Дата", "Магазин", "Сума (₴)"])
    for r in receipts:
        writer.writerow([
            timezone.localtime(r.datetime).strftime("%d.%m.%Y %H:%M"),
            str(r.store),
            str(r.total),
        ])

    return response


@login_required
def manual(request):
    categories = Category.objects.order_by("name")
    recipients = list(Recipient.objects.values_list("name", flat=True))
    return render(request, "receipts/manual.html", {
        "categories": categories,
        "now": timezone.now(),
        "recipients": recipients,
    })


# ---------------------------------------------------------------------------
# Analytics
# ---------------------------------------------------------------------------

@login_required
def analytics(request):
    today = timezone.now().date()
    from_date_str = request.GET.get("from_date", today.replace(day=1).isoformat())
    to_date_str = request.GET.get("to_date", today.isoformat())
    mode = request.GET.get("mode", "all")
    recipient_id = request.GET.get("recipient_id", "")

    try:
        from_date = datetime.date.fromisoformat(from_date_str)
        to_date = datetime.date.fromisoformat(to_date_str)
    except ValueError:
        from_date = today.replace(day=1)
        to_date = today

    base_items = Item.objects.filter(
        receipt__user=request.user,
        receipt__datetime__date__gte=from_date,
        receipt__datetime__date__lte=to_date,
    )

    if mode == "personal":
        base_items = base_items.filter(for_recipient__isnull=True)
    elif mode == "gifts":
        base_items = base_items.filter(for_recipient__isnull=False)
        if recipient_id:
            base_items = base_items.filter(for_recipient_id=recipient_id)

    # Use Item aggregations for store/month when any filter is active
    filtered = mode != "all" or bool(recipient_id)

    by_category = list(
        base_items.exclude(category=None)
        .values("category__name")
        .annotate(total=Sum("total"))
        .order_by("-total")
    )

    if filtered:
        by_store = list(
            base_items
            .values("receipt__store__display_name", "receipt__store__legal_name")
            .annotate(total=Sum("total"))
            .order_by("-total")
        )
        by_month = list(
            base_items
            .annotate(month=TruncMonth("receipt__datetime"))
            .values("month")
            .annotate(total=Sum("total"))
            .order_by("month")
        )
        def store_label(row):
            return row["receipt__store__display_name"] or row["receipt__store__legal_name"] or "Unknown"
    else:
        base_receipts = Receipt.objects.filter(
            user=request.user,
            datetime__date__gte=from_date,
            datetime__date__lte=to_date,
        )
        by_store = list(
            base_receipts
            .values("store__display_name", "store__legal_name")
            .annotate(total=Sum("total"))
            .order_by("-total")
        )
        by_month = list(
            base_receipts
            .annotate(month=TruncMonth("datetime"))
            .values("month")
            .annotate(total=Sum("total"))
            .order_by("month")
        )
        def store_label(row):
            return row["store__display_name"] or row["store__legal_name"] or "Unknown"

    recipients = Recipient.objects.all()

    return render(request, "receipts/analytics.html", {
        "from_date": from_date_str,
        "to_date": to_date_str,
        "mode": mode,
        "recipient_id": recipient_id,
        "recipients": recipients,
        "cat_data": json.dumps({
            "labels": [r["category__name"] for r in by_category],
            "values": [float(r["total"]) for r in by_category],
        }),
        "store_data": json.dumps({
            "labels": [store_label(r) for r in by_store],
            "values": [float(r["total"]) for r in by_store],
        }),
        "month_data": json.dumps({
            "labels": [r["month"].strftime("%Y-%m") if r["month"] else "" for r in by_month],
            "values": [float(r["total"]) for r in by_month],
        }),
    })

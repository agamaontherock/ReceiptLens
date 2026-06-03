from django.shortcuts import render, redirect, get_object_or_404
from django.db import transaction
from django.utils.dateparse import parse_datetime
from django.db.models import Sum
from django.db.models.functions import TruncMonth
from django.utils import timezone
import datetime
import json

from .models import Store, Category, Receipt, Item, CategoryRule
from .services.dps import parse_qr_url, fetch_check
from .services.tax_check import parse_chk_response
from .services.categorize import suggest_category_id, learn_rule


def dashboard(request):
    recent = Receipt.objects.select_related("store").order_by("-datetime")[:10]
    now = timezone.now()
    this_month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    last_month_end = this_month_start - datetime.timedelta(seconds=1)
    last_month_start = last_month_end.replace(day=1, hour=0, minute=0, second=0, microsecond=0)

    this_month_total = Receipt.objects.filter(datetime__gte=this_month_start).aggregate(
        total=Sum("total"))["total"] or 0
    last_month_total = Receipt.objects.filter(
        datetime__gte=last_month_start, datetime__lte=last_month_end
    ).aggregate(total=Sum("total"))["total"] or 0

    top_categories = (Item.objects
        .filter(receipt__datetime__gte=this_month_start)
        .exclude(category=None)
        .values("category__name")
        .annotate(total=Sum("total"))
        .order_by("-total")[:3])

    return render(request, "receipts/dashboard.html", {
        "recent": recent,
        "this_month_total": this_month_total,
        "last_month_total": last_month_total,
        "top_categories": top_categories,
    })


def scan(request):
    return render(request, "receipts/scan.html")


def receipts_parse(request):
    qr_url = request.POST.get("qr_url", "").strip()
    xml_b64 = request.POST.get("check_xml_b64", "").strip()
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
        items = [{
            "code": it.code, "barcode": it.barcode, "name": it.name,
            "qty": str(it.qty), "unit": it.unit,
            "unit_price": str(it.unit_price), "total": str(it.total),
            "suggested_category_id": suggest_category_id(barcode=it.barcode, name=it.name) or "",
        } for it in receipt.items]
        return render(request, "receipts/_review.html", {
            "receipt": receipt, "store": store,
            "categories": categories, "items": items,
        })
    except Exception as e:
        return render(request, "receipts/_review_error.html", {"error": str(e)})


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
    item_count = int(request.POST.get("item_count", 0))

    store, _ = Store.objects.update_or_create(
        fiscal_rro=fiscal_rro,
        defaults={"legal_name": legal_name, "display_name": display_name},
    )

    receipt_dt = parse_datetime(receipt_datetime_str)
    if receipt_dt is None:
        receipt_dt = timezone.now()
    if timezone.is_naive(receipt_dt):
        receipt_dt = timezone.make_aware(receipt_dt)

    receipt = Receipt.objects.create(
        store=store,
        check_id=check_id,
        fn=fn,
        datetime=receipt_dt,
        total=total_str or 0,
        source=source,
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

        Item.objects.create(
            receipt=receipt,
            code=code,
            barcode=barcode,
            name=name,
            qty=qty,
            unit=unit,
            unit_price=unit_price,
            total=total,
            category_id=category_id,
        )

        if category_id:
            learn_rule(barcode=barcode, name=name, category_id=category_id)

    return redirect("receipt_detail", pk=receipt.pk)


def receipt_delete(request, pk):
    receipt = get_object_or_404(Receipt, pk=pk)
    if request.method == "POST":
        receipt.delete()
        return redirect("dashboard")
    return redirect("receipt_detail", pk=pk)


def receipt_detail(request, pk):
    receipt = get_object_or_404(Receipt.objects.select_related("store"), pk=pk)
    items = receipt.items.select_related("category").all()
    return render(request, "receipts/receipt_detail.html", {"receipt": receipt, "items": items})


def manual(request):
    categories = Category.objects.order_by("name")
    return render(request, "receipts/manual.html", {
        "categories": categories,
        "now": timezone.now(),
    })


def analytics(request):
    today = timezone.now().date()
    from_date_str = request.GET.get("from_date", today.replace(day=1).isoformat())
    to_date_str = request.GET.get("to_date", today.isoformat())

    try:
        from_date = datetime.date.fromisoformat(from_date_str)
        to_date = datetime.date.fromisoformat(to_date_str)
    except ValueError:
        from_date = today.replace(day=1)
        to_date = today

    base_items = Item.objects.filter(
        receipt__datetime__date__gte=from_date,
        receipt__datetime__date__lte=to_date,
    )
    base_receipts = Receipt.objects.filter(
        datetime__date__gte=from_date,
        datetime__date__lte=to_date,
    )

    by_category = list(
        base_items.exclude(category=None)
        .values("category__name")
        .annotate(total=Sum("total"))
        .order_by("-total")
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

    cat_data = {"labels": [r["category__name"] for r in by_category],
                "values": [float(r["total"]) for r in by_category]}
    store_data = {"labels": [store_label(r) for r in by_store],
                  "values": [float(r["total"]) for r in by_store]}
    month_data = {"labels": [r["month"].strftime("%Y-%m") if r["month"] else "" for r in by_month],
                  "values": [float(r["total"]) for r in by_month]}

    return render(request, "receipts/analytics.html", {
        "from_date": from_date_str,
        "to_date": to_date_str,
        "cat_data": json.dumps(cat_data),
        "store_data": json.dumps(store_data),
        "month_data": json.dumps(month_data),
    })

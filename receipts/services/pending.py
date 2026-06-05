from datetime import timedelta

from django.db import transaction
from django.utils import timezone
from django.utils.dateparse import parse_datetime

from .dps import parse_qr_url, fetch_check
from .tax_check import parse_chk_response
from .categorize import suggest_category_id


def _next_retry_at(pending_import):
    """Compute next retry datetime based on retry_count. Returns None if all retries exhausted."""
    from receipts.models import PENDING_IMPORT_RETRY_DELAYS_HOURS
    idx = pending_import.retry_count
    if idx >= len(PENDING_IMPORT_RETRY_DELAYS_HOURS):
        return None
    return pending_import.created_at + timedelta(hours=PENDING_IMPORT_RETRY_DELAYS_HOURS[idx])


def _record_failure(pending_import, error: str) -> None:
    from receipts.models import PendingImport
    pending_import.retry_count += 1
    pending_import.last_error = error[:1000]
    next_at = _next_retry_at(pending_import)
    if next_at is None:
        pending_import.status = PendingImport.FAILED
        pending_import.next_retry_at = None
    else:
        pending_import.status = PendingImport.PENDING
        pending_import.next_retry_at = next_at
    pending_import.save(update_fields=["status", "retry_count", "next_retry_at", "last_error", "updated_at"])


def process_pending_import(pending_import) -> bool:
    """
    Attempt to fetch and save the receipt for a pending import.
    Handles PENDING and FAILED states; uses an atomic claim to prevent duplicate processing.
    Returns True on success, False on failure.
    """
    from receipts.models import PendingImport, Store, Receipt, Item

    claimed = (
        PendingImport.objects
        .filter(pk=pending_import.pk, status__in=[PendingImport.PENDING, PendingImport.FAILED])
        .update(status=PendingImport.PROCESSING)
    )
    if not claimed:
        return False

    try:
        p = parse_qr_url(pending_import.qr_url)
        data = fetch_check(id=p["id"], fn=p["fn"], sm=p["sm"], api_date=p["api_date"])
        receipt_data = parse_chk_response(data)
    except Exception as e:
        _record_failure(pending_import, str(e))
        return False

    try:
        with transaction.atomic():
            store, _ = Store.objects.update_or_create(
                fiscal_rro=receipt_data.fiscal_rro,
                defaults={"legal_name": receipt_data.org_legal_name},
            )
            dt = parse_datetime(receipt_data.datetime.replace(" ", "T"))
            if dt is None:
                dt = timezone.now()
            elif timezone.is_naive(dt):
                dt = timezone.make_aware(dt)

            receipt = Receipt.objects.create(
                user=pending_import.user,
                store=store,
                check_id=receipt_data.order_num,
                fn=receipt_data.fiscal_rro,
                datetime=dt,
                total=receipt_data.total,
                source="pending",
                qr_url=pending_import.qr_url,
            )
            for it in receipt_data.items:
                cat_id = suggest_category_id(barcode=it.barcode, name=it.name)
                Item.objects.create(
                    receipt=receipt,
                    code=it.code, barcode=it.barcode,
                    name=it.name, qty=it.qty, unit=it.unit,
                    unit_price=it.unit_price, total=it.total,
                    category_id=cat_id,
                )

            pending_import.status = PendingImport.PROCESSED
            pending_import.receipt = receipt
            pending_import.last_error = ""
            pending_import.save(update_fields=["status", "receipt", "last_error", "updated_at"])
        return True
    except Exception as e:
        _record_failure(pending_import, str(e))
        return False


def process_user_due_imports(user) -> int:
    """Process all due pending imports for a user. Returns count of successful fetches."""
    from receipts.models import PendingImport
    due = list(
        PendingImport.objects.filter(
            user=user,
            status=PendingImport.PENDING,
            next_retry_at__lte=timezone.now(),
        )
    )
    return sum(1 for pi in due if process_pending_import(pi))


def process_all_due_imports() -> tuple[int, int]:
    """Process all due pending imports across all users. Returns (success, failure) counts."""
    from receipts.models import PendingImport
    due = list(
        PendingImport.objects.select_related("user").filter(
            status=PendingImport.PENDING,
            next_retry_at__lte=timezone.now(),
        )
    )
    success = failure = 0
    for pi in due:
        if process_pending_import(pi):
            success += 1
        else:
            failure += 1
    return success, failure

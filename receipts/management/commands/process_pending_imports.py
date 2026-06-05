from django.core.management.base import BaseCommand
from django.utils import timezone

from receipts.models import PendingImport
from receipts.services.pending import process_pending_import


class Command(BaseCommand):
    help = "Process all pending receipt imports that are due for retry"

    def handle(self, *args, **options):
        now = timezone.now()
        due = list(
            PendingImport.objects.select_related("user").filter(
                status=PendingImport.PENDING,
                next_retry_at__lte=now,
            )
        )
        if not due:
            self.stdout.write("No pending imports due.")
            return

        self.stdout.write(f"Processing {len(due)} pending import(s)...")
        success = failure = 0
        for pi in due:
            if process_pending_import(pi):
                success += 1
                self.stdout.write(self.style.SUCCESS(
                    f"  OK  #{pi.pk} user={pi.user_id} url={pi.qr_url[:60]}"
                ))
            else:
                pi.refresh_from_db()
                failure += 1
                self.stdout.write(self.style.WARNING(
                    f"  FAIL #{pi.pk} [{pi.status}] retry={pi.retry_count} error={pi.last_error[:80]}"
                ))

        self.stdout.write(self.style.SUCCESS(
            f"Done: {success} fetched, {failure} failed/rescheduled."
        ))

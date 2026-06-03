from django.core.management.base import BaseCommand, CommandError
from receipts.services.dps import parse_qr_url, fetch_check
from receipts.services.tax_check import parse_chk_response


class Command(BaseCommand):
    help = "Fetch and pretty-print a receipt from a QR URL"

    def add_arguments(self, parser):
        parser.add_argument("qr_url", type=str, help="QR URL from a Ukrainian fiscal receipt")

    def handle(self, *args, **options):
        url = options["qr_url"]
        try:
            params = parse_qr_url(url)
            self.stdout.write(f"Parsed QR params: {params}")
            data = fetch_check(**params)
            receipt = parse_chk_response(data)
        except Exception as exc:
            raise CommandError(str(exc)) from exc

        self.stdout.write(self.style.SUCCESS("\n=== RECEIPT ==="))
        self.stdout.write(f"Org:      {receipt.org_legal_name}")
        self.stdout.write(f"Point:    {receipt.point_name}")
        self.stdout.write(f"Address:  {receipt.point_address}")
        self.stdout.write(f"РРО:      {receipt.fiscal_rro}")
        self.stdout.write(f"Order #:  {receipt.order_num}")
        self.stdout.write(f"DateTime: {receipt.datetime}")
        self.stdout.write(f"Total:    {receipt.total} ₴")
        self.stdout.write(f"\n{'#':<4} {'Name':<40} {'Qty':>8} {'Unit':<4} {'Price':>10} {'Total':>10}")
        self.stdout.write("-" * 80)
        for i, item in enumerate(receipt.items, 1):
            self.stdout.write(
                f"{i:<4} {item.name:<40} {item.qty:>8} {item.unit:<4} "
                f"{item.unit_price:>10} {item.total:>10}"
            )
        self.stdout.write("-" * 80)
        self.stdout.write(f"{'Items:':<54} {len(receipt.items):>4}  {'Total:':>10} {receipt.total:>10} ₴")

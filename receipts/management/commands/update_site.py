import os
from django.core.management.base import BaseCommand
from django.contrib.sites.models import Site


class Command(BaseCommand):
    help = "Set the Site domain to match ALLOWED_HOSTS (needed by django-allauth)"

    def handle(self, *args, **options):
        allowed = os.environ.get("ALLOWED_HOSTS", "").split()
        domain = next((h for h in allowed if "onrender.com" in h or "." in h), "localhost")
        domain = domain.lstrip(".")  # strip leading wildcard dot if present
        site, _ = Site.objects.update_or_create(
            id=1,
            defaults={"domain": domain, "name": "ReceiptLens"},
        )
        self.stdout.write(self.style.SUCCESS(f"Site domain set to: {site.domain}"))

from django.core.management.base import BaseCommand
from receipts.models import CategoryRule


class Command(BaseCommand):
    help = "Print all learned categorization rules"

    def handle(self, *args, **options):
        rules = CategoryRule.objects.select_related("category").order_by("key_type", "key_value")
        if not rules.exists():
            self.stdout.write("No rules found.")
            return
        self.stdout.write(f"\n{'Type':<8} {'Key':<50} {'Category'}")
        self.stdout.write("-" * 80)
        for rule in rules:
            self.stdout.write(f"{rule.key_type:<8} {rule.key_value:<50} {rule.category.name}")
        self.stdout.write(f"\nTotal: {rules.count()} rules")

from django.core.management.base import BaseCommand
from receipts.models import Category

CATEGORIES = [
    "Овочі",
    "Фрукти",
    "Молочне",
    "М'ясо та ковбаси",
    "Випічка та хліб",
    "Бакалія",
    "Снеки та солодощі",
    "Напої",
    "Побутове",
    "Інше",
]


class Command(BaseCommand):
    help = "Create default food categories (idempotent)"

    def handle(self, *args, **options):
        created = 0
        for name in CATEGORIES:
            _, was_created = Category.objects.get_or_create(name=name)
            if was_created:
                created += 1
        self.stdout.write(self.style.SUCCESS(
            f"Done. {created} new categories created, {len(CATEGORIES) - created} already existed."
        ))

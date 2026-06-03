from django.core.management.base import BaseCommand
from receipts.models import Category, CategoryRule

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

SEEDS = [
    # Молочне
    ("молок", "Молочне"), ("сир", "Молочне"), ("масл", "Молочне"),
    ("сметан", "Молочне"), ("кефір", "Молочне"), ("йогурт", "Молочне"),
    ("вершк", "Молочне"), ("творог", "Молочне"),
    # М'ясо та ковбаси
    ("ковбас", "М'ясо та ковбаси"), ("сардел", "М'ясо та ковбаси"),
    ("шинк", "М'ясо та ковбаси"), ("кабанос", "М'ясо та ковбаси"),
    ("сосиск", "М'ясо та ковбаси"), ("м'яс", "М'ясо та ковбаси"),
    # Випічка та хліб
    ("хліб", "Випічка та хліб"), ("батон", "Випічка та хліб"),
    ("булк", "Випічка та хліб"), ("завиван", "Випічка та хліб"),
    # Овочі / Фрукти
    ("картопл", "Овочі"), ("цибул", "Овочі"), ("морков", "Овочі"),
    ("яблук", "Фрукти"), ("банан", "Фрукти"), ("апельсин", "Фрукти"),
    # Снеки та солодощі
    ("батончик", "Снеки та солодощі"), ("шоколад", "Снеки та солодощі"),
    ("цукерк", "Снеки та солодощі"), ("чипс", "Снеки та солодощі"),
    # Напої
    ("вода", "Напої"), ("сік", "Напої"), ("напій", "Напої"),
    # Бакалія
    ("крупа", "Бакалія"), ("гречк", "Бакалія"), ("рис", "Бакалія"),
    ("макарон", "Бакалія"), ("олія", "Бакалія"),
]

class Command(BaseCommand):
    help = "Create default food categories (idempotent)"

    def handle(self, *args, **options):
        cat_created = 0
        for name in CATEGORIES:
            _, was_created = Category.objects.get_or_create(name=name)
            if was_created:
                cat_created += 1

        rules_created = 0
        for keyword, cat_name in SEEDS:
            cat = Category.objects.get(name=cat_name)
            _, was_created = CategoryRule.objects.get_or_create(
                key_type=CategoryRule.KEYWORD,
                key_value=keyword.upper(),
                defaults={"category": cat},
            )
            if was_created:
                rules_created += 1

        self.stdout.write(self.style.SUCCESS(
            f"Done. {cat_created} categories and {rules_created} keyword rules created "
            f"({len(CATEGORIES) - cat_created} categories and "
            f"{len(SEEDS) - rules_created} rules already existed)."
        ))

from pathlib import Path
import yaml
from django.core.management.base import BaseCommand
from receipts.models import Category, CategoryRule

SEEDS_DIR = Path(__file__).resolve().parents[2] / "seeds"

CATEGORIES = [
    "Овочі", "Фрукти", "Молочне", "Сир", "М'ясо та ковбаси",
    "Випічка та хліб", "Бакалія", "Снеки та солодощі", "Напої",
    "Побутове", 'Морепродукти', 'Кулінарія', "Інше",
]


class Command(BaseCommand):
    help = "Create default categories and seed keyword rules from receipts/seeds/*.yaml"

    def handle(self, *args, **options):
        # Categories
        cat_created = 0
        for name in CATEGORIES:
            _, created = Category.objects.get_or_create(name=name)
            if created:
                cat_created += 1

        # Seed files
        seed_files = sorted(SEEDS_DIR.glob("*.yaml"))
        if not seed_files:
            self.stdout.write(self.style.WARNING(f"No seed files found in {SEEDS_DIR}"))

        total_created = total_skipped = 0
        for path in seed_files:
            created, skipped = self._load_file(path)
            total_created += created
            total_skipped += skipped
            self.stdout.write(
                f"  {path.name}: {created} rules created, {skipped} already existed"
            )

        self.stdout.write(self.style.SUCCESS(
            f"Done. {cat_created} new categories. "
            f"{total_created} keyword rules created, {total_skipped} already existed "
            f"across {len(seed_files)} file(s)."
        ))

    def _load_file(self, path: Path) -> tuple[int, int]:
        with open(path, encoding="utf-8") as f:
            data = yaml.safe_load(f)

        keywords: dict = data.get("keywords", {})
        created = skipped = 0
        for cat_name, keyword_list in keywords.items():
            try:
                cat = Category.objects.get(name=cat_name)
            except Category.DoesNotExist:
                self.stdout.write(self.style.WARNING(
                    f"    Category {cat_name!r} not found — skipping {len(keyword_list)} rule(s)"
                ))
                continue
            for keyword in keyword_list:
                keyword = str(keyword).strip()
                if not keyword:
                    continue
                _, was_created = CategoryRule.objects.get_or_create(
                    key_type=CategoryRule.KEYWORD,
                    key_value=keyword.upper(),
                    defaults={"category": cat},
                )
                if was_created:
                    created += 1
                else:
                    skipped += 1
        return created, skipped

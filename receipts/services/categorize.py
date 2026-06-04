from django.db.models.functions import Length
from receipts.models import CategoryRule


def normalize_name(name: str) -> str:
    # Some POS systems (METRO) use Latin I (U+0049) where Ukrainian І (U+0406) is expected.
    # Normalize to Ukrainian so keywords match regardless of source encoding.
    return " ".join(name.upper().replace("I", "І").split())


def suggest_category_id(*, barcode: str, name: str) -> int | None:
    if barcode:
        rule = CategoryRule.objects.filter(key_type="barcode", key_value=barcode).first()
        if rule:
            return rule.category_id

    rule = CategoryRule.objects.filter(
        key_type="name", key_value=normalize_name(name)).first()
    if rule:
        return rule.category_id

    # Keyword fallback: check if any seeded keyword appears in the item name.
    # Longer keywords are checked first so "батончик" beats "батон".
    normalized = normalize_name(name)
    keyword_rules = (CategoryRule.objects
                     .filter(key_type="keyword")
                     .only("key_value", "category_id")
                     .order_by(Length("key_value").desc()))
    for rule in keyword_rules:
        if rule.key_value in normalized:
            return rule.category_id

    return None


def learn_rule(*, barcode: str, name: str, category_id: int) -> None:
    key_type, key_value = ("barcode", barcode) if barcode else ("name", normalize_name(name))
    CategoryRule.objects.update_or_create(
        key_type=key_type, key_value=key_value,
        defaults={"category_id": category_id})

from receipts.models import CategoryRule


def normalize_name(name: str) -> str:
    return " ".join(name.upper().split())


def suggest_category_id(*, barcode: str, name: str) -> int | None:
    if barcode:
        rule = CategoryRule.objects.filter(key_type="barcode", key_value=barcode).first()
        if rule:
            return rule.category_id
    rule = CategoryRule.objects.filter(
        key_type="name", key_value=normalize_name(name)).first()
    return rule.category_id if rule else None


def learn_rule(*, barcode: str, name: str, category_id: int) -> None:
    key_type, key_value = ("barcode", barcode) if barcode else ("name", normalize_name(name))
    CategoryRule.objects.update_or_create(
        key_type=key_type, key_value=key_value,
        defaults={"category_id": category_id})

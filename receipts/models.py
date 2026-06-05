from django.conf import settings
from django.db import models


class UserProfile(models.Model):
    COUNTRY_CHOICES = [
        ("UA", "Україна"),
        ("PL", "Польща"),
        ("DE", "Німеччина"),
        ("GB", "Велика Британія"),
        ("US", "США"),
        ("OTHER", "Інша"),
    ]

    user = models.OneToOneField(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="profile"
    )
    nickname = models.CharField(max_length=64, blank=True)
    country = models.CharField(max_length=8, choices=COUNTRY_CHOICES, blank=True)
    avatar = models.ImageField(upload_to="avatars/", blank=True, null=True)

    def __str__(self):
        return self.nickname or self.user.email

    def display_name(self):
        return self.nickname or self.user.username


class Store(models.Model):
    fiscal_rro = models.CharField(max_length=32, unique=True, db_index=True)
    legal_name = models.CharField(max_length=255, blank=True)
    display_name = models.CharField(max_length=255, blank=True)

    def __str__(self):
        return self.display_name or self.legal_name or self.fiscal_rro


class Category(models.Model):
    name = models.CharField(max_length=64, unique=True)

    def __str__(self):
        return self.name

    class Meta:
        verbose_name_plural = "categories"


class Receipt(models.Model):
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE,
        related_name="receipts", null=True, blank=True,
    )
    store = models.ForeignKey(Store, on_delete=models.CASCADE, related_name="receipts")
    check_id = models.CharField(max_length=64, blank=True)
    fn = models.CharField(max_length=32, blank=True)
    datetime = models.DateTimeField(db_index=True)
    total = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    source = models.CharField(max_length=16, default="qr")  # qr | xml | manual
    qr_url = models.URLField(max_length=512, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["user", "fn", "check_id"],
                condition=models.Q(fn__gt="") & models.Q(check_id__gt=""),
                name="unique_receipt_per_user",
            )
        ]

    def __str__(self):
        return f"{self.store} {self.datetime:%Y-%m-%d} {self.total} ₴"


class Item(models.Model):
    receipt = models.ForeignKey(Receipt, on_delete=models.CASCADE, related_name="items")
    code = models.CharField(max_length=32, blank=True)
    barcode = models.CharField(max_length=64, blank=True, db_index=True)
    name = models.CharField(max_length=255)
    qty = models.DecimalField(max_digits=12, decimal_places=3, default=0)
    unit = models.CharField(max_length=8, default="pcs")
    unit_price = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    total = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    category = models.ForeignKey(Category, null=True, blank=True,
                                 on_delete=models.SET_NULL, related_name="items")

    def __str__(self):
        return f"{self.name} ({self.qty} {self.unit})"


PENDING_IMPORT_RETRY_DELAYS_HOURS = [2, 4, 8, 16, 24]


class PendingImport(models.Model):
    PENDING = "pending"
    PROCESSING = "processing"
    PROCESSED = "processed"
    FAILED = "failed"
    STATUS_CHOICES = [
        (PENDING, "Очікує"),
        (PROCESSING, "Обробляється"),
        (PROCESSED, "Оброблено"),
        (FAILED, "Помилка"),
    ]

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="pending_imports"
    )
    qr_url = models.URLField(max_length=512)
    status = models.CharField(max_length=16, choices=STATUS_CHOICES, default=PENDING, db_index=True)
    retry_count = models.IntegerField(default=0)
    next_retry_at = models.DateTimeField(null=True, blank=True, db_index=True)
    last_error = models.TextField(blank=True)
    receipt = models.OneToOneField(
        Receipt, null=True, blank=True, on_delete=models.SET_NULL, related_name="pending_import"
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"PendingImport #{self.pk} [{self.status}] {self.qr_url[:60]}"

    def status_display(self):
        return dict(self.STATUS_CHOICES).get(self.status, self.status)


class CategoryRule(models.Model):
    BARCODE = "barcode"
    NAME = "name"
    KEYWORD = "keyword"
    key_type = models.CharField(max_length=8, default=BARCODE)
    key_value = models.CharField(max_length=255, db_index=True)
    category = models.ForeignKey(Category, on_delete=models.CASCADE)

    class Meta:
        unique_together = ("key_type", "key_value")

    def __str__(self):
        return f"{self.key_type}:{self.key_value} → {self.category}"

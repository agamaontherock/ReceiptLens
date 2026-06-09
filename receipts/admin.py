from django.contrib import admin
from .models import Store, Category, Receipt, Item, CategoryRule, Recipient


@admin.register(Recipient)
class RecipientAdmin(admin.ModelAdmin):
    list_display = ["name", "created_at"]
    search_fields = ["name"]


@admin.register(Store)
class StoreAdmin(admin.ModelAdmin):
    list_display = ("fiscal_rro", "display_name", "legal_name")
    search_fields = ("fiscal_rro", "display_name")


@admin.register(Category)
class CategoryAdmin(admin.ModelAdmin):
    list_display = ("name",)


@admin.register(Receipt)
class ReceiptAdmin(admin.ModelAdmin):
    list_display = ("store", "datetime", "total", "for_recipient", "source")
    list_filter = ("source", "store", "for_recipient")
    date_hierarchy = "datetime"


@admin.register(Item)
class ItemAdmin(admin.ModelAdmin):
    list_display = ("name", "barcode", "category", "for_recipient", "total")
    search_fields = ("name", "barcode")
    list_filter = ("category", "for_recipient")


@admin.register(CategoryRule)
class CategoryRuleAdmin(admin.ModelAdmin):
    list_display = ("key_type", "key_value", "category")
    search_fields = ("key_value",)
    list_filter = ("key_type",)

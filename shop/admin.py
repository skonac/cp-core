from django.contrib import admin

from .models import BillingProfile, Product, Purchase


@admin.register(Product)
class ProductAdmin(admin.ModelAdmin):
    """What we sell. Disable (is_active) instead of deleting: purchases point at their product."""
    list_display = ("id", "name_tr", "name_en", "title_tr", "credits", "price", "currency", "is_active", "is_featured",
                    "sort_order")
    list_display_links = ("id", "name_tr")
    list_editable = ("is_active", "is_featured", "sort_order")
    fieldsets = (
        ("Türkçe", {"fields": ("name_tr", "title_tr", "description_tr")}),
        ("English", {"fields": ("name_en", "title_en", "description_en")}),
        (None, {"fields": ("credits", "price", "currency", "is_active", "is_featured", "sort_order")}),
    )


@admin.register(BillingProfile)
class BillingProfileAdmin(admin.ModelAdmin):
    list_display = ("id", "user", "type", "first_name", "last_name", "company_name", "city", "updated_at")
    list_filter = ("type",)
    search_fields = ("user__phone", "first_name", "last_name", "company_name", "tax_number", "email")
    raw_id_fields = ("user",)


@admin.register(Purchase)
class PurchaseAdmin(admin.ModelAdmin):
    """Purchases and the payment provider's ids: read-only (credits come from the payment flow)."""
    list_display = ("id", "created_at", "user", "product_name_tr", "credits", "amount", "currency", "status",
                    "provider", "provider_payment_id")
    list_filter = ("status", "provider")
    search_fields = ("user__phone", "conversation_id", "provider_payment_id")
    readonly_fields = [f.name for f in Purchase._meta.fields]

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

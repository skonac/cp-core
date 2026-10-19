from django.contrib import admin

from .models import Product, Purchase


@admin.register(Product)
class ProductAdmin(admin.ModelAdmin):
    """What we sell. Disable (is_active) instead of deleting: purchases point at their product."""
    list_display = ("id", "name", "title", "credits", "price", "currency", "is_active", "is_featured", "sort_order")
    list_display_links = ("id", "name")
    list_editable = ("is_active", "is_featured", "sort_order")
    fields = ("name", "title", "description", "credits", "price", "currency", "is_active", "is_featured", "sort_order")


@admin.register(Purchase)
class PurchaseAdmin(admin.ModelAdmin):
    """Purchases and the payment provider's ids: read-only (credits come from the payment flow)."""
    list_display = ("id", "created_at", "user", "product_name", "credits", "amount", "currency", "status",
                    "provider", "provider_payment_id")
    list_filter = ("status", "provider")
    search_fields = ("user__phone", "conversation_id", "provider_payment_id")
    readonly_fields = [f.name for f in Purchase._meta.fields]

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

from django.contrib import admin
from django.utils.html import format_html
from django.utils.timezone import localtime

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
    exclude = ("docs_html",)          # ~40 KB of HTML: opened through the "documents" link instead
    readonly_fields = ["documents"] + [f.name for f in Purchase._meta.fields if f.name != "docs_html"]

    @admin.display(description="Documents (ÖBF + MSS)")
    def documents(self, obj):
        if not obj or not obj.docs_html:
            return "–"
        return format_html('<a href="/dj/staff/purchases/{}/documents/" target="_blank">Open the stored page</a> '
                           '(version {}, accepted {})', obj.pk, obj.docs_version,
                           localtime(obj.docs_accepted_at).strftime("%Y-%m-%d %H:%M:%S") if obj.docs_accepted_at else "?")

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

"""Credits are adjusted by hand here until a payment provider exists: adding a transaction
moves the user's balance in the same database transaction. Saved transactions are read-only."""
from django.contrib import admin
from django.db import transaction
from django.db.models import F

from accounts.models import User

from .models import CreditPackage, CreditTransaction, Purchase


@admin.register(CreditPackage)
class CreditPackageAdmin(admin.ModelAdmin):
    """What we sell. Disable (is_active) instead of deleting: purchases point at their package."""
    list_display = ("id", "name", "title", "credits", "price", "currency", "is_active", "is_featured", "sort_order")
    list_display_links = ("id", "name")
    list_editable = ("is_active", "is_featured", "sort_order")
    fields = ("name", "title", "description", "credits", "price", "currency", "is_active", "is_featured", "sort_order")


@admin.register(Purchase)
class PurchaseAdmin(admin.ModelAdmin):
    """Purchase attempts and the payment provider's ids: read-only (credits come from the payment flow)."""
    list_display = ("id", "created_at", "user", "package_name", "credits", "amount", "currency", "status",
                    "provider", "provider_payment_id")
    list_filter = ("status", "provider")
    search_fields = ("user__phone", "conversation_id", "provider_payment_id")
    readonly_fields = [f.name for f in Purchase._meta.fields]

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(CreditTransaction)
class CreditTransactionAdmin(admin.ModelAdmin):
    list_display = ("id", "user", "delta", "kind", "ref", "actor", "note", "created_at")
    list_filter = ("kind",)
    search_fields = ("user__phone", "ref", "note")
    raw_id_fields = ("user",)
    fields = ("user", "delta", "kind", "ref", "note")

    def has_change_permission(self, request, obj=None):
        return obj is None   # history is not edited; correct a mistake with a new adjustment

    def has_delete_permission(self, request, obj=None):
        return False

    def save_model(self, request, obj, form, change):
        with transaction.atomic():
            obj.actor = request.user
            obj.save()
            # F() = "balance + delta" computed by Postgres, safe against concurrent uploads.
            User.objects.filter(pk=obj.user_id).update(credit_balance=F("credit_balance") + obj.delta)

"""Credits are adjusted by hand here until a payment provider exists: adding a transaction
moves the user's balance in the same database transaction. Saved transactions are read-only."""
from django.contrib import admin
from django.db import transaction
from django.db.models import F

from accounts.models import User

from .models import CreditTransaction


@admin.register(CreditTransaction)
class CreditTransactionAdmin(admin.ModelAdmin):
    list_display = ("user", "delta", "kind", "ref", "actor", "note", "created_at")
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

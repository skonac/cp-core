from django.contrib import admin

from .models import Notification, PushDevice


@admin.register(Notification)
class NotificationAdmin(admin.ModelAdmin):
    """The temporary send log: read-only."""
    list_display = ("id", "created_at", "kind", "channel", "to", "status", "delivery", "provider", "attempts", "error")
    list_filter = ("status", "delivery", "channel", "kind", "provider")
    search_fields = ("to", "user__phone", "event__code", "provider_ref")
    raw_id_fields = ("user", "event", "device")

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False


@admin.register(PushDevice)
class PushDeviceAdmin(admin.ModelAdmin):
    list_display = ("id", "user", "kind", "user_agent", "created_at", "last_success_at", "failures")
    list_filter = ("kind",)
    search_fields = ("user__phone",)
    raw_id_fields = ("user",)
    readonly_fields = ("endpoint", "keys")

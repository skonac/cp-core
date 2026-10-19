from django.contrib import admin

from .models import Worker


@admin.register(Worker)
class WorkerAdmin(admin.ModelAdmin):
    list_display = ("id", "worker_id", "version", "ip", "last_seen_at")
    readonly_fields = ("worker_id", "version", "ip", "last_seen_at")

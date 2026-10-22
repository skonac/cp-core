from django.contrib import admin

from .models import DownloadLog, Face, FaceRejection, Image


@admin.register(Image)
class ImageAdmin(admin.ModelAdmin):
    list_display = ("id", "kind", "event", "seq_str", "owner", "status", "num_faces", "attempts",
                    "error", "created_at", "deleted_at")
    list_filter = ("kind", "status", "error")
    search_fields = ("event__code", "owner__phone", "sha256")
    raw_id_fields = ("event", "owner")
    readonly_fields = ("sha256", "width", "height", "bytes", "taken_at", "exif", "engine", "processed_at",
                       "worker", "processing_until", "created_at")


@admin.register(Face)
class FaceAdmin(admin.ModelAdmin):
    list_display = ("id", "image", "score", "engine")
    raw_id_fields = ("image",)
    exclude = ("embedding",)   # 128 numbers: useless in a form
    readonly_fields = ("bbox", "landmarks", "score", "engine")


@admin.register(FaceRejection)
class FaceRejectionAdmin(admin.ModelAdmin):
    """"Not me" clicks: read-only here (users make and undo them)."""
    list_display = ("id", "user", "face", "event", "similarity", "created_at")
    search_fields = ("user__phone", "event__code")
    raw_id_fields = ("user", "face", "event")

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False


@admin.register(DownloadLog)
class DownloadLogAdmin(admin.ModelAdmin):
    list_display = ("id", "user", "image", "downloaded_at", "ip")
    search_fields = ("user__phone",)
    raw_id_fields = ("user", "image")

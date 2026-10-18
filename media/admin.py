from django.contrib import admin

from .models import DownloadLog, Face, Image


@admin.register(Image)
class ImageAdmin(admin.ModelAdmin):
    list_display = ("id", "is_selfie", "event", "seq_str", "owner", "status", "num_faces", "attempts",
                    "error", "created_at", "deleted_at")
    list_filter = ("is_selfie", "status", "error")
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


@admin.register(DownloadLog)
class DownloadLogAdmin(admin.ModelAdmin):
    list_display = ("user", "image", "downloaded_at", "ip")
    search_fields = ("user__phone",)
    raw_id_fields = ("user", "image")

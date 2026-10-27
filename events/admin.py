from django.contrib import admin

from .models import Event, EventMember


@admin.register(Event)
class EventAdmin(admin.ModelAdmin):
    list_display_links = ("id", "display_code")
    list_display = ("id", "display_code", "name", "type", "date", "owner", "status",
                    "is_processing_faces", "is_browse_all", "is_consent_required", "created_at")
    list_filter = ("status", "type", "is_processing_faces", "is_browse_all", "is_consent_required")
    search_fields = ("code", "name", "owner__phone")
    raw_id_fields = ("owner", "cover_image", "frame_landscape_image", "frame_portrait_image")
    readonly_fields = ("code", "next_seq", "last_claimed_at", "frame_changed_at", "created_at", "updated_at")


@admin.register(EventMember)
class EventMemberAdmin(admin.ModelAdmin):
    list_display = ("id", "user", "event", "joined_at", "last_activity_at")
    search_fields = ("user__phone", "event__code", "event__name")
    raw_id_fields = ("user", "event")

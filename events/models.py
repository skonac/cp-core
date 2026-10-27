"""Event and EventMember. Fields and rules: design/core.md."""
from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import Q
from django.utils import timezone

from .codes import new_event_code


class Event(models.Model):
    class Status(models.TextChoices):
        OPEN = "open"          # new events start here; participants have access
        CLOSED = "closed"      # owner closed access; uploads and processing continue
        EXPIRED = "expired"    # final: media removed or moved, nothing works except metadata

    class Type(models.TextChoices):
        WEDDING = "wedding"
        SPORTS = "sports"
        GRADUATION = "graduation"
        CORPORATE = "corporate"
        OTHER = "other"

    code = models.CharField(max_length=8, unique=True, editable=False)
    name = models.CharField(max_length=200)
    type = models.CharField(max_length=16, choices=Type.choices, default=Type.WEDDING)
    date = models.DateField()
    city = models.CharField(max_length=120, blank=True)      # "İstanbul"
    venue = models.CharField(max_length=120, blank=True)     # "Çırağan Sarayı"; a place name, never a street address
    owner = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="owned_events")
    status = models.CharField(max_length=8, choices=Status.choices, default=Status.OPEN)

    is_processing_faces = models.BooleanField(default=True)    # False: photos never go to the worker
    is_browse_all = models.BooleanField(default=False)         # members may browse/download every photo
    is_consent_required = models.BooleanField(default=True)    # False: members without consent get browse-all only

    cover_image = models.ForeignKey("media.Image", null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    frame_landscape_image = models.ForeignKey("media.Image", null=True, blank=True, on_delete=models.SET_NULL,
                                              related_name="+")
    frame_portrait_image = models.ForeignKey("media.Image", null=True, blank=True, on_delete=models.SET_NULL,
                                             related_name="+")
    frame_changed_at = models.DateTimeField(null=True, blank=True, editable=False)   # guards a render racing a change
    next_seq = models.PositiveIntegerField(default=1, editable=False)     # next photo number (00001, 00002, ...)
    last_claimed_at = models.DateTimeField(null=True, blank=True, editable=False)   # worker round-robin

    created_at = models.DateTimeField(default=timezone.now, editable=False)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            # No face processing means the only way to see photos is browse-all.
            models.CheckConstraint(condition=Q(is_processing_faces=True) | Q(is_browse_all=True),
                                   name="event_no_faces_needs_browse_all"),
        ]

    def clean(self):
        # Same rule as the constraint, but as a readable form error in the admin.
        if not self.is_processing_faces and not self.is_browse_all:
            raise ValidationError("An event without face processing must allow browsing all photos.")

    def save(self, *args, **kwargs):
        if not self.code:
            # 28.6 M codes: a collision is rare; retry a few times, the unique index is the final guard.
            for _ in range(10):
                code = new_event_code()
                if not Event.objects.filter(code=code).exists():
                    self.code = code
                    break
        super().save(*args, **kwargs)

    @property
    def location_label(self) -> str:
        """"Çırağan Sarayı, İstanbul": venue and city, either may be missing."""
        return ", ".join(part for part in (self.venue, self.city) if part)

    @property
    def display_code(self) -> str:
        return self.code.upper()

    def __str__(self):
        return f"{self.display_code} {self.name}"


class EventMember(models.Model):
    """A participant who has accessed an event: the participant's "your events" list.
    Organizers' own events come from Event.owner, not from here."""
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="memberships")
    event = models.ForeignKey(Event, on_delete=models.CASCADE, related_name="members")
    joined_at = models.DateTimeField(default=timezone.now)
    last_activity_at = models.DateTimeField(default=timezone.now)   # updated at most once an hour

    class Meta:
        constraints = [models.UniqueConstraint(fields=["user", "event"], name="member_once_per_event")]
        indexes = [models.Index(fields=["user", "-last_activity_at"], name="member_recent")]

    def __str__(self):
        return f"{self.user} @ {self.event.display_code}"

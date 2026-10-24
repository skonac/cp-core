"""Image (event photos, selfies, event covers; also the face-worker queue), Face, FaceRejection, DownloadLog.
design/core.md."""
from django.conf import settings
from django.contrib.postgres.fields import ArrayField
from django.db import models
from django.db.models import Q
from django.utils import timezone
from pgvector.django import VectorField

UNFINISHED = ["pending", "processing"]


class Image(models.Model):
    class Status(models.TextChoices):
        PENDING = "pending"          # waiting for a worker
        PROCESSING = "processing"    # claimed; belongs to `worker` until processing_until
        DONE = "done"                # result stored (num_faces may be 0)
        FAILED = "failed"            # gave up: unreadable, or WORKER_MAX_ATTEMPTS reached

    class Kind(models.TextChoices):
        PHOTO = "photo"     # event photo: numbered (seq), charged 1 credit, worker task all_faces
        SELFIE = "selfie"   # a user's face profile: no event, slot primary/secondary, worker task main_face
        COVER = "cover"     # event cover uploaded by the organizer: free, never processed, not in the gallery

    class Slot(models.TextChoices):
        PRIMARY = "primary"
        SECONDARY = "secondary"

    class Rendition(models.TextChoices):
        MASTER = "m"
        DISPLAY = "d"
        THUMB = "t"

    kind = models.CharField(max_length=8, choices=Kind.choices, default=Kind.PHOTO)
    event = models.ForeignKey("events.Event", null=True, blank=True, on_delete=models.CASCADE, related_name="images")
    owner = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="images")
    # Photos an uploader uploaded for the organizer (owner = the organizer, who pays): which access.
    uploaded_by = models.ForeignKey("accounts.UploaderAccess", null=True, blank=True, on_delete=models.SET_NULL,
                                    related_name="images")
    seq = models.PositiveIntegerField(null=True, blank=True)        # photos: 44 -> "00044"
    slot = models.CharField(max_length=10, choices=Slot.choices, blank=True)   # selfies only

    status = models.CharField(max_length=10, choices=Status.choices, default=Status.PENDING)
    processing_until = models.DateTimeField(null=True, blank=True)
    worker = models.CharField(max_length=64, blank=True)
    attempts = models.PositiveSmallIntegerField(default=0)
    error = models.CharField(max_length=32, blank=True)             # worker error code, kept for review
    error_detail = models.TextField(blank=True)
    num_faces = models.PositiveSmallIntegerField(null=True, blank=True)
    engine = models.CharField(max_length=100, blank=True)
    processed_at = models.DateTimeField(null=True, blank=True)

    width = models.PositiveIntegerField(null=True, blank=True)      # of the master
    height = models.PositiveIntegerField(null=True, blank=True)
    bytes = models.PositiveIntegerField(null=True, blank=True)
    sha256 = models.CharField(max_length=64)                        # of the uploaded bytes
    taken_at = models.DateTimeField(null=True, blank=True)          # EXIF DateTimeOriginal
    exif = models.JSONField(null=True, blank=True)

    created_at = models.DateTimeField(default=timezone.now)
    deleted_at = models.DateTimeField(null=True, blank=True)        # soft delete; files and faces go at once

    class Meta:
        constraints = [
            # A photo belongs to an event and has a number; a selfie has neither; a cover has an event, no number.
            models.CheckConstraint(
                condition=Q(kind="photo", event__isnull=False, seq__isnull=False)
                | Q(kind="selfie", event__isnull=True, seq__isnull=True)
                | Q(kind="cover", event__isnull=False, seq__isnull=True),
                name="image_kind_shape"),
            models.UniqueConstraint(fields=["event", "seq"], name="image_seq_per_event"),
            # The same file twice in one event is rejected (and not charged).
            models.UniqueConstraint(fields=["event", "sha256"], condition=Q(kind="photo", deleted_at__isnull=True),
                                    name="image_no_duplicate_per_event"),
            # One live selfie per slot per user; uploading to a slot replaces (soft-deletes) the old one.
            models.UniqueConstraint(fields=["owner", "slot"], condition=Q(kind="selfie", deleted_at__isnull=True),
                                    name="selfie_one_per_slot"),
        ]
        indexes = [
            # The worker queue: small, because finished rows drop out of these partial indexes.
            models.Index(fields=["event", "created_at"], condition=Q(status__in=UNFINISHED), name="image_queue"),
            models.Index(fields=["created_at"], condition=Q(kind="selfie", status__in=UNFINISHED), name="selfie_queue"),
        ]

    @property
    def is_selfie(self) -> bool:
        return self.kind == self.Kind.SELFIE

    @property
    def is_cover(self) -> bool:
        return self.kind == self.Kind.COVER

    @property
    def seq_str(self) -> str:
        return f"{self.seq:05d}" if self.seq is not None else ""

    def relpath(self, rendition: str) -> str:
        """File path under MEDIA_ROOT: events/<event_id>/00044_m.jpg, events/<event_id>/cover_<id>_d.jpg
        or selfies/<user_id>/<id>_d.jpg. Covers and selfies have no master (_d and _t only)."""
        if self.is_selfie:
            return f"selfies/{self.owner_id}/{self.pk}_{rendition}.jpg"
        if self.is_cover:
            return f"events/{self.event_id}/cover_{self.pk}_{rendition}.jpg"
        return f"events/{self.event_id}/{self.seq_str}_{rendition}.jpg"

    def __str__(self):
        if self.is_selfie:
            return f"selfie {self.pk} ({self.owner_id})"
        return f"{self.event_id}/cover {self.pk}" if self.is_cover else f"{self.event_id}/{self.seq_str}"


class Face(models.Model):
    """One detected face. Coordinates are normalized 0–1 (faceworker.md). Matching uses only
    the embedding; bbox, landmarks and score are for quality checks, highlighting, tuning."""
    image = models.ForeignKey(Image, on_delete=models.CASCADE, related_name="faces")
    bbox = ArrayField(models.FloatField(), size=4)          # x1, y1, x2, y2
    landmarks = ArrayField(models.FloatField(), size=10)    # 5 points: x, y pairs
    score = models.FloatField()                             # detector confidence
    embedding = VectorField(dimensions=128)                 # unit length: cosine = dot product
    engine = models.CharField(max_length=100)

    def __str__(self):
        return f"face {self.pk} of {self.image}"


class FaceRejection(models.Model):
    """A user's "not me" on one face. Used by matching as a negative example (media/matching.py):
    the face, and faces closer to it than to the user, are no longer theirs. Counts in every event.
    Deleted with the face (photo deleted) or the user."""
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="face_rejections")
    face = models.ForeignKey(Face, on_delete=models.CASCADE, related_name="rejections")
    event = models.ForeignKey("events.Event", on_delete=models.CASCADE, related_name="face_rejections")
    similarity = models.FloatField(null=True, blank=True)   # to the user's selfie at rejection time (tuning data)
    created_at = models.DateTimeField(default=timezone.now)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["user", "face"], name="face_rejection_once")]

    def __str__(self):
        return f"{self.user_id} not face {self.face_id}"


class DownloadLog(models.Model):
    """One row per master download (a ZIP writes one per photo). Also the source of the per-photo cap."""
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="downloads")
    image = models.ForeignKey(Image, on_delete=models.CASCADE, related_name="downloads")
    downloaded_at = models.DateTimeField(default=timezone.now)
    ip = models.GenericIPAddressField(null=True, blank=True)

    class Meta:
        indexes = [models.Index(fields=["user", "image", "downloaded_at"], name="download_cap")]

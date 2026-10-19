"""Accepting uploads. The single place where a file becomes an Image row, and for event
photos the single place where a credit is spent (design/core.md, "Photo upload and credits").

    ingest_photo(event, uploader, data) -> Image     raises IngestError(code)
    ingest_selfie(user, data, slot)     -> Image     raises IngestError(code)
    ingest_cover(event, uploader, data) -> Image     raises IngestError(code); free, never processed
"""
import hashlib
import io
import logging
import math
from datetime import datetime, timedelta, timezone as dt_timezone

from django.conf import settings
from django.db import IntegrityError, transaction
from django.db.models import F
from django.utils import timezone
from PIL import ExifTags, Image as PILImage, ImageOps, UnidentifiedImageError
from pillow_heif import register_heif_opener

from accounts.models import User
from events.models import Event

from .models import Image
from .storage import abspath, soft_delete, write_file

register_heif_opener()   # lets Pillow open HEIC/HEIF (iPhone photos)
log = logging.getLogger("cp.ingest")

ACCEPTED_FORMATS = {"JPEG", "MPO", "PNG", "WEBP", "HEIF"}   # MPO: phone JPEGs with an extra embedded image
SKIP_EXIF_TAGS = {0x8769, 0x8825, 0xA005, 0x927C, 0x9286, 0x0201, 0x0202}   # pointers, GPS, maker note, comment, thumbnail


class IngestError(Exception):
    """code is a stable machine-readable reason, also used as the API error code."""

    def __init__(self, code: str, detail: str = ""):
        super().__init__(f"{code}: {detail}" if detail else code)
        self.code, self.detail = code, detail


# ---------------------------------------------------------------------------- decoding

def _jsonable(value):
    """EXIF value -> something JSON can hold, or None to drop it."""
    if isinstance(value, bytes):
        return None
    if isinstance(value, str):
        value = value.strip("\x00 ").strip()
        return value if len(value) <= 500 else None
    if isinstance(value, (bool, int)):
        return value
    if isinstance(value, float) or hasattr(value, "numerator"):   # IFDRational
        try:
            number = float(value)
        except (ZeroDivisionError, ValueError):
            return None
        # A 0/0 rational ("unknown", e.g. a lens's max aperture) gives NaN; JSON has no NaN/Infinity.
        return round(number, 6) if math.isfinite(number) else None
    if isinstance(value, (tuple, list)):
        items = [_jsonable(v) for v in value]   # unknown items stay as null: [24.0, 70.0, null, null]
        return items if any(v is not None for v in items) else None
    return None


def _taken_at(exif_ifd: dict, main: dict):
    """EXIF DateTimeOriginal as an aware datetime. Cameras store local time without a zone;
    when OffsetTimeOriginal is missing we assume the server's zone (Europe/Istanbul)."""
    raw = exif_ifd.get(0x9003) or main.get(0x0132)
    if not isinstance(raw, str):
        return None
    try:
        naive = datetime.strptime(raw.strip("\x00").strip(), "%Y:%m:%d %H:%M:%S")
    except ValueError:
        return None
    offset = exif_ifd.get(0x9011)   # "+03:00"
    if isinstance(offset, str) and len(offset.strip()) >= 6:
        try:
            sign = -1 if offset.strip()[0] == "-" else 1
            hours, minutes = offset.strip()[1:6].split(":")
            tz = dt_timezone(sign * timedelta(hours=int(hours), minutes=int(minutes)))
            return naive.replace(tzinfo=tz)
        except ValueError:
            pass
    return timezone.make_aware(naive)


def _decode(data: bytes):
    """bytes -> (RGB image upright, exif dict for JSON, taken_at, icc profile)."""
    try:
        im = PILImage.open(io.BytesIO(data))
        if im.format not in ACCEPTED_FORMATS:
            raise IngestError("unsupported_file_type", str(im.format))
        im.load()
    except IngestError:
        raise
    except (UnidentifiedImageError, OSError, ValueError, PILImage.DecompressionBombError) as e:
        raise IngestError("unreadable_image", type(e).__name__) from None

    exif = im.getexif()
    main = dict(exif)
    exif_ifd = dict(exif.get_ifd(ExifTags.IFD.Exif))
    info = {}
    for tags in (main, exif_ifd):          # GPS IFD is never read: location is not stored
        for tag, value in tags.items():
            if tag in SKIP_EXIF_TAGS:
                continue
            v = _jsonable(value)
            if v is not None:
                info[ExifTags.TAGS.get(tag, str(tag))] = v
    taken_at = _taken_at(exif_ifd, main)

    icc = im.info.get("icc_profile")
    im = ImageOps.exif_transpose(im)       # apply the camera's rotation flag to the pixels
    if im.mode != "RGB":
        im = im.convert("RGB")
    return im, info, taken_at, icc


def _encode(im, side: int, quality: int, icc) -> tuple[bytes, int, int]:
    """Shrink so the long edge is at most `side` (never enlarge), JPEG-encode. No EXIF is
    written, so GPS and everything else is stripped from the renditions."""
    copy = im.copy()
    copy.thumbnail((side, side), PILImage.Resampling.LANCZOS)
    buf = io.BytesIO()
    copy.save(buf, "JPEG", quality=quality, icc_profile=icc)
    return buf.getvalue(), copy.width, copy.height


def _check_size(data: bytes):
    if not data:
        raise IngestError("file_required")
    if len(data) > settings.UPLOAD_MAX_BYTES:
        raise IngestError("file_too_large", f"{len(data)} bytes")


def _remove(paths):
    for p in paths:
        p.unlink(missing_ok=True)


# ---------------------------------------------------------------------------- photos

def ingest_photo(event: Event, uploader: User, data: bytes) -> Image:
    """Validate, make renditions, charge the event owner 1 credit, store. All or nothing."""
    _check_size(data)
    if event.status == Event.Status.EXPIRED:
        raise IngestError("event_expired")
    sha = hashlib.sha256(data).hexdigest()
    if Image.objects.filter(event=event, kind=Image.Kind.PHOTO, sha256=sha, deleted_at__isnull=True).exists():
        raise IngestError("duplicate_photo")

    im, exif, taken_at, icc = _decode(data)
    master, width, height = _encode(im, settings.MASTER_SIDE, settings.MASTER_QUALITY, icc)
    display, _, _ = _encode(im, settings.DISPLAY_SIDE, settings.DISPLAY_QUALITY, icc)
    thumb, _, _ = _encode(im, settings.THUMB_SIDE, settings.THUMB_QUALITY, icc)

    written = []
    try:
        with transaction.atomic():
            # 1. Credit: one UPDATE that only succeeds while the balance stays at or above the floor.
            #    Postgres row-locks the owner until commit, so parallel uploads queue up here.
            payer = User.objects.get(pk=event.owner_id)
            charged = (User.objects.filter(pk=payer.pk, credit_balance__gt=payer.effective_credit_floor)
                       .update(credit_balance=F("credit_balance") - 1))
            if not charged:
                raise IngestError("insufficient_credits")
            # 2. Photo number: lock the event row, take next_seq, bump it.
            seq = Event.objects.select_for_update().values_list("next_seq", flat=True).get(pk=event.pk)
            Event.objects.filter(pk=event.pk).update(next_seq=F("next_seq") + 1)
            # 3. Row + files. Any failure from here rolls back the credit and the number too.
            image = Image.objects.create(
                event_id=event.pk, owner=uploader, seq=seq, sha256=sha, width=width, height=height,
                bytes=len(master), taken_at=taken_at, exif=exif or None)
            for rendition, blob in (("m", master), ("d", display), ("t", thumb)):
                written.append(write_file(image.relpath(rendition), blob))
    except IntegrityError:
        _remove(written)
        raise IngestError("duplicate_photo") from None   # same file uploaded twice at the same moment
    except BaseException:
        _remove(written)
        raise
    log.info("photo %s accepted (%sx%s)", image, width, height)
    return image


# ---------------------------------------------------------------------------- selfies

def ingest_selfie(user: User, data: bytes, slot: str = Image.Slot.PRIMARY) -> Image:
    """Store a selfie in a slot, replacing the slot's previous selfie. Requires consent."""
    if not user.is_consent:
        raise IngestError("consent_required")
    if slot not in Image.Slot.values:
        raise IngestError("invalid_slot", slot)
    _check_size(data)
    sha = hashlib.sha256(data).hexdigest()
    im, _, _, icc = _decode(data)           # selfie EXIF is not kept
    display, width, height = _encode(im, settings.DISPLAY_SIDE, settings.DISPLAY_QUALITY, icc)
    thumb, _, _ = _encode(im, settings.THUMB_SIDE, settings.THUMB_QUALITY, icc)

    written = []
    try:
        with transaction.atomic():
            for old in Image.objects.select_for_update().filter(owner=user, kind=Image.Kind.SELFIE, slot=slot,
                                                                deleted_at__isnull=True):
                soft_delete(old)
            image = Image.objects.create(kind=Image.Kind.SELFIE, owner=user, slot=slot, sha256=sha,
                                         width=width, height=height, bytes=len(display))
            for rendition, blob in (("d", display), ("t", thumb)):
                written.append(write_file(image.relpath(rendition), blob))
    except BaseException:
        _remove(written)
        raise
    log.info("selfie %s accepted for %s (%s)", image.pk, user, slot)
    return image


# ---------------------------------------------------------------------------- covers

def ingest_cover(event: Event, uploader: User, data: bytes) -> Image:
    """The event's cover picture, uploaded by the organizer: free (no credit), never sent to the
    worker, not in the gallery. Replaces the previous uploaded cover and becomes event.cover_image."""
    _check_size(data)
    if event.status == Event.Status.EXPIRED:
        raise IngestError("event_expired")
    sha = hashlib.sha256(data).hexdigest()
    im, _, _, icc = _decode(data)           # cover EXIF is not kept
    display, width, height = _encode(im, settings.DISPLAY_SIDE, settings.DISPLAY_QUALITY, icc)
    thumb, _, _ = _encode(im, settings.THUMB_SIDE, settings.THUMB_QUALITY, icc)

    written = []
    try:
        with transaction.atomic():
            for old in Image.objects.select_for_update().filter(event=event, kind=Image.Kind.COVER,
                                                                deleted_at__isnull=True):
                soft_delete(old)
            image = Image.objects.create(kind=Image.Kind.COVER, event_id=event.pk, owner=uploader, sha256=sha,
                                         status=Image.Status.DONE, width=width, height=height, bytes=len(display))
            for rendition, blob in (("d", display), ("t", thumb)):
                written.append(write_file(image.relpath(rendition), blob))
            Event.objects.filter(pk=event.pk).update(cover_image=image)
            event.cover_image = image
    except BaseException:
        _remove(written)
        raise
    log.info("cover %s accepted for event %s", image.pk, event.code)
    return image


def event_cover(event: Event) -> Image | None:
    """What to show as the event's picture: the chosen cover, else the first photo."""
    cover = event.cover_image
    if cover is not None and cover.deleted_at is None:
        return cover
    return (Image.objects.filter(event=event, kind=Image.Kind.PHOTO, deleted_at__isnull=True)
            .order_by("seq").first())

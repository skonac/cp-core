"""Event frames on photos (design/core.md, "Next step: event frames, rendering + preview").

    served_relpath(image, "d" | "m") -> the file to serve: framed when the event has a frame for the
                                        photo's orientation (rendered here on first use), else the plain one
    clear_framed(event)              -> after a frame change or removal: drop the event's framed files

A photo takes the frame of its orientation (square = landscape); an empty slot = no frame. The frame
PNG is stretched to the photo's size and laid over it (overlay only: size and face boxes unchanged).
Framed files sit next to the plain ones: 00044_fd.jpg (display), 00044_fm.jpg (master). Rendering is
lazy; a framed file older than event.frame_changed_at counts as missing (guards a render that raced a
frame change). _t, and _d as the face worker's input, stay unframed.
"""
import io
import logging
from pathlib import Path

from django.conf import settings
from django.db import transaction
from PIL import Image as PILImage

from .storage import abspath, write_file

log = logging.getLogger("cp.media")

FRAMED = {"d": "fd", "m": "fm"}


def _quality(rendition: str) -> int:
    return settings.DISPLAY_QUALITY if rendition == "d" else settings.MASTER_QUALITY


def frame_for(event, image):
    """The event's frame Image for this photo's orientation, or None."""
    landscape = (image.width or 0) >= (image.height or 0)
    field = "frame_landscape_image" if landscape else "frame_portrait_image"
    if getattr(event, f"{field}_id") is None:
        return None
    return getattr(event, field)


def _fresh(path: Path, changed_at) -> bool:
    if not path.is_file():
        return False
    return changed_at is None or path.stat().st_mtime >= changed_at.timestamp()


def render(photo_path: Path, frame_path: Path, quality: int) -> bytes:
    """The photo with the frame stretched over it, as JPEG (the photo's ICC profile kept)."""
    with PILImage.open(photo_path) as photo:
        icc = photo.info.get("icc_profile")
        base = photo.convert("RGBA")
    with PILImage.open(frame_path) as frame:
        overlay = frame.convert("RGBA")
        if overlay.size != base.size:
            overlay = overlay.resize(base.size, PILImage.Resampling.LANCZOS)
    base.alpha_composite(overlay)
    buf = io.BytesIO()
    base.convert("RGB").save(buf, "JPEG", quality=quality, icc_profile=icc)
    return buf.getvalue()


def served_relpath(image, rendition: str, event=None) -> str:
    """What to serve for a photo's display ("d") or master ("m"). Falls back to the plain file when
    there is no frame, the source is missing, or rendering fails (logged)."""
    plain = image.relpath(rendition)
    if rendition not in FRAMED or image.kind != "photo":
        return plain
    event = event or image.event
    frame = frame_for(event, image)
    if frame is None:
        return plain
    framed = image.relpath(FRAMED[rendition])
    if _fresh(abspath(framed), event.frame_changed_at):
        return framed
    source = abspath(plain)
    if not source.is_file():
        return plain
    try:
        data = render(source, abspath(frame.relpath()), _quality(rendition))
    except (OSError, ValueError) as e:
        log.warning("image %s: framing %s with frame %s failed: %s", image.pk, rendition, frame.pk, e)
        return plain
    write_file(framed, data)
    # The frame changed while rendering: this file used the old one, drop it (the next request renders again).
    current = type(event).objects.filter(pk=event.pk).values_list("frame_changed_at", flat=True).first()
    if current != event.frame_changed_at:
        abspath(framed).unlink(missing_ok=True)
        return plain
    return framed


def framed_paths(event) -> list[Path]:
    folder = abspath(f"events/{event.pk}")
    return [p for pattern in ("*_fd.jpg", "*_fm.jpg") for p in folder.glob(pattern)] if folder.is_dir() else []


def clear_framed(event) -> None:
    """Remove the event's framed files once the current transaction commits."""
    transaction.on_commit(lambda: [p.unlink(missing_ok=True) for p in framed_paths(event)])

"""Files under MEDIA_ROOT: writing renditions, serving them, removing them.

Serving: in production nginx sends the bytes (X-Accel-Redirect to an internal location),
so Python only decides who may see a file. In dev (CP_X_ACCEL=0) Django streams it.
"""
import os
from pathlib import Path

from django.conf import settings
from django.db import transaction
from django.http import FileResponse, Http404, HttpResponse
from django.utils import timezone


def abspath(relpath: str) -> Path:
    return Path(settings.MEDIA_ROOT) / relpath


def write_file(relpath: str, data: bytes) -> Path:
    """Write via a temp file + rename, so a reader never sees half a JPEG."""
    path = abspath(relpath)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_bytes(data)
    os.replace(tmp, path)
    return path


def file_response(relpath: str, download_name: str | None = None) -> HttpResponse:
    if settings.USE_X_ACCEL:
        response = HttpResponse(content_type="image/jpeg")
        response["X-Accel-Redirect"] = settings.X_ACCEL_PREFIX + relpath
    else:
        path = abspath(relpath)
        if not path.is_file():
            raise Http404
        response = FileResponse(path.open("rb"), content_type="image/jpeg")
    if download_name:
        response["Content-Disposition"] = f'attachment; filename="{download_name}"'
    return response


def soft_delete(image) -> None:
    """Delete an image the way the product does: row marked, faces and files gone.
    Call inside a transaction; the files are removed only if it commits."""
    image.deleted_at = timezone.now()
    image.save(update_fields=["deleted_at"])
    image.faces.all().delete()
    paths = [abspath(image.relpath(r)) for r in ("m", "d", "t")]
    transaction.on_commit(lambda: [p.unlink(missing_ok=True) for p in paths])

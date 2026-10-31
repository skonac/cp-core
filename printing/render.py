"""Fill a print theme with an event's data: HTML (the preview, shown by the browser) and PDF (WeasyPrint,
from the same HTML). design/core.md "Printed materials".

The HTML references theme files under ASSETS_URL (/api/print/assets/<theme>/…, public: no event data) and
the event's cover by a URL the caller gives (it needs the caller's access check). For the PDF, WeasyPrint
reads those same URLs from disk through url_fetcher: nothing goes over HTTP, and any other URL is refused.
"""
import logging
import mimetypes
import threading
from pathlib import Path
from urllib.parse import urlsplit

import segno
from django.conf import settings
from django.template import Context, Engine
from django.utils.safestring import mark_safe

from . import themes

log = logging.getLogger("cp.printing")
ASSETS_URL = "/api/print/assets/"
PDF_BASE = "https://print.invalid/"        # WeasyPrint's base URL; url_fetcher maps paths, never fetches
MONTHS = {"tr": ["Ocak", "Şubat", "Mart", "Nisan", "Mayıs", "Haziran", "Temmuz", "Ağustos", "Eylül", "Ekim",
                 "Kasım", "Aralık"],
          "en": ["January", "February", "March", "April", "May", "June", "July", "August", "September",
                 "October", "November", "December"]}
_renders = threading.BoundedSemaphore(settings.PRINT_RENDERS_AT_ONCE)


class RenderBusy(Exception):
    """Too many PDF renders at once (each can take 100-200 MB for a moment)."""


def format_date(d, lang: str) -> str:
    return f"{d.day} {MONTHS.get(lang, MONTHS['tr'])[d.month - 1]} {d.year}"


def name_size(name: str) -> str:
    """Size class for the event name: themes define .s .m .l .xl (test with a 60-character name)."""
    n = len(name)
    return "s" if n <= 14 else "m" if n <= 22 else "l" if n <= 36 else "xl"


def qr_svg(join_url: str, dark: str) -> str:
    """The join URL as inline SVG (viewBox only: the theme sizes .qr-code in CSS). Error correction M,
    4-module quiet zone, white light modules."""
    svg = segno.make(join_url, error="m").svg_inline(scale=1, border=4, dark=dark, light="#ffffff", omitsize=True)
    return svg.replace("<svg ", '<svg class="qr-code" preserveAspectRatio="xMidYMid meet" ', 1)


def cover_path(event) -> Path | None:
    """The uploaded cover's display rendition on disk, or None (then the theme's fallback image)."""
    cover = event.cover_image
    if cover is None or cover.deleted_at is not None:
        return None
    path = Path(settings.MEDIA_ROOT) / cover.relpath("d")
    return path if path.is_file() else None


def _engine() -> Engine:
    # Not cached: theme edits show up on the next render, no restart (renders are rare).
    return Engine(dirs=[str(themes.root())], autoescape=True, debug=settings.DEBUG)


def render_html(theme: themes.Theme, fmt: str, event, lang: str, join_url: str, cover_url: str | None) -> str:
    """The filled page. cover_url: where the browser gets the event's cover (None: no uploaded cover)."""
    width, height, names = themes.FORMATS[fmt]
    assets = ASSETS_URL
    photo = cover_url or (f"{assets}{theme.id}/{theme.fallback_image}" if theme.fallback_image else "")
    context = {
        "lang": lang, "event": event, "date": format_date(event.date, lang), "code": event.display_code,
        "join_url": join_url, "qr": mark_safe(qr_svg(join_url, theme.qr_dark)), "photo": photo,
        "name_size": name_size(event.name), "t": theme.text(lang), "assets": assets,
        "asset": f"{assets}{theme.id}/", "theme_name": theme.name.get(lang, theme.id),
        "fmt": {"id": fmt, "width": width, "height": height, "name": names.get(lang, fmt)},
    }
    return _engine().get_template(f"{theme.id}/{fmt}.html").render(Context(context))


def _fetcher(cover_url: str | None, cover_file: Path | None):
    root = themes.root().resolve()

    def fetch(url, *args, **kwargs):
        path = urlsplit(url).path
        target = None
        if path.startswith(ASSETS_URL):
            candidate = (root / path[len(ASSETS_URL):]).resolve()
            if candidate.is_relative_to(root) and candidate.is_file():
                target = candidate
        elif cover_url and cover_file and path == urlsplit(cover_url).path:
            target = cover_file
        if target is None:
            raise ValueError(f"print render: refused {url}")
        return {"file_obj": target.open("rb"), "mime_type": mimetypes.guess_type(target.name)[0] or
                "application/octet-stream", "redirected_url": url}
    return fetch


def render_pdf(html: str, cover_url: str | None = None, cover_file: Path | None = None) -> bytes:
    """The same HTML as the preview, as PDF. Raises RenderBusy when PRINT_RENDERS_AT_ONCE are running."""
    import weasyprint   # heavy import, only where PDFs are made

    if not _renders.acquire(blocking=False):
        raise RenderBusy
    try:
        return weasyprint.HTML(string=html, base_url=PDF_BASE, encoding="utf-8",
                               url_fetcher=_fetcher(cover_url, cover_file)).write_pdf()
    finally:
        _renders.release()

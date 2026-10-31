"""Print theme files (public) and the staff design page.

    /api/print/assets/<path>                  theme files: fonts, CSS, images (no event data; public)
    /dj/staff/print/                          every theme x format, filled with a real event and two samples
    /dj/staff/print/<theme>/<format>.html     the filled page   ?event=<code>|short|long  &lang=tr|en
    /dj/staff/print/<theme>/<format>.pdf      the same as PDF (WeasyPrint)

The organizer endpoints (cp-web) come in step 2: design/core.md "Printed materials".
"""
import mimetypes
from datetime import date

from django.http import FileResponse, Http404, HttpResponse
from django.shortcuts import get_object_or_404, render
from django.urls import reverse

from events.codes import normalize_code
from events.models import Event
from staff.views import staff_required

from . import render as R
from . import themes

ASSET_TYPES = {".css", ".woff2", ".woff", ".ttf", ".otf", ".jpg", ".jpeg", ".png", ".svg", ".webp"}
SAMPLES = {   # unsaved events for design work: a short and a 60-character name, no cover (theme fallback)
    "short": dict(name="Derya & Can", city="İstanbul", venue="Çırağan Sarayı"),
    "long": dict(name="Ayşe Nur Yılmaz & Mehmet Can Öztürk'ün Düğün Töreni ve Kınası", city="Muğla",
                 venue="Bodrum Yalıkavak Marina Yacht Club"),
}


def asset(request, path):
    """A file from print_themes/ for the browser preview. Only static asset types, never outside the folder."""
    root = themes.root().resolve()
    target = (root / path).resolve()
    if not target.is_relative_to(root) or target.suffix.lower() not in ASSET_TYPES or not target.is_file():
        raise Http404
    response = FileResponse(target.open("rb"), content_type=mimetypes.guess_type(target.name)[0])
    response["Cache-Control"] = "public, max-age=3600"
    return response


def _sample(kind: str) -> Event:
    return Event(code="rk238", type="wedding", date=date(2026, 9, 12), **SAMPLES[kind])


def _event_and_cover(request, which: str):
    """(event, cover_url, cover_file) for ?event=: a sample name or an event code."""
    if which in SAMPLES:
        return _sample(which), None, None
    event = get_object_or_404(Event, code=normalize_code(which))
    file = R.cover_path(event)
    url = reverse("staff:image_file", args=[event.cover_image_id, "d"]) if file else None
    return event, url, file


def _theme_and_format(theme_id: str, fmt: str):
    theme = themes.get(theme_id)
    if theme is None or fmt not in theme.formats:
        raise Http404
    return theme


def _lang(request) -> str:
    lang = request.GET.get("lang", "tr")
    return lang if lang in themes.LANGS else "tr"


@staff_required
def page(request):
    lang = _lang(request)
    latest = Event.objects.order_by("-created_at").first()
    chosen = request.GET.get("event") or (latest.display_code if latest else "short")
    sources = [chosen] + [s for s in SAMPLES if s != chosen]
    rows = [{"theme": t, "name": t.name.get(lang, t.id),
             "formats": [{"id": f, "name": themes.FORMATS[f][2][lang], "width": themes.FORMATS[f][0],
                          "height": themes.FORMATS[f][1],
                          "items": [{"source": s,
                                     "url": reverse("staff:print_html", args=[t.id, f]) + f"?event={s}&lang={lang}",
                                     "pdf": reverse("staff:print_pdf", args=[t.id, f]) + f"?event={s}&lang={lang}"}
                                    for s in sources]}
                         for f in t.formats]}
            for t in themes.all_themes()]
    return render(request, "printing/staff_print.html", {"rows": rows, "lang": lang, "chosen": chosen,
                                                         "events": Event.objects.order_by("-created_at")[:30]})


@staff_required
def html(request, theme_id, fmt):
    theme = _theme_and_format(theme_id, fmt)
    event, cover_url, _ = _event_and_cover(request, request.GET.get("event", "short"))
    join_url = request.build_absolute_uri(f"/?event={event.display_code}")
    return HttpResponse(R.render_html(theme, fmt, event, _lang(request), join_url, cover_url))


@staff_required
def pdf(request, theme_id, fmt):
    theme = _theme_and_format(theme_id, fmt)
    event, cover_url, cover_file = _event_and_cover(request, request.GET.get("event", "short"))
    join_url = request.build_absolute_uri(f"/?event={event.display_code}")
    page_html = R.render_html(theme, fmt, event, _lang(request), join_url, cover_url)
    try:
        data = R.render_pdf(page_html, cover_url, cover_file)
    except R.RenderBusy:
        return HttpResponse("Busy: other PDFs are being rendered, try again in a few seconds.", status=429)
    response = HttpResponse(data, content_type="application/pdf")
    response["Content-Disposition"] = f'inline; filename="{event.display_code}-{theme.id}-{fmt}.pdf"'
    return response

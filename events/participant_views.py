"""Participant pages (Django templates), under /dj/. Login is phone + password until SMS sign-in exists.

    /e/<code>/                          event: my photos (+ all photos if the event allows)
    /e/<code>/photos/<seq>/             one photo, download button
    /e/<code>/photos/00017_t.jpg (_d)   files, checked per request
    /e/<code>/photos/<seq>/download     full size; logged; capped per photo per day
    /me/                                my events
    /me/face/                           my selfies: upload / replace, status
    /me/face/<id>_t.jpg                 selfie thumbnail (owner only)
    /consent/                           KVKK explicit consent
"""
from datetime import timedelta

from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.http import url_has_allowed_host_and_scheme

from accounts import kvkk
from media.framing import served_relpath
from media.ingest import IngestError, ingest_selfie
from media.matching import my_photos
from media.models import DownloadLog, Image
from media.storage import file_response

from .codes import normalize_code
from .models import Event, EventMember
from .participant import access_for, may_see_photo, touch_membership

INGEST_MESSAGES = {
    "consent_required": "Please give consent first.",
    "unreadable_image": "That file could not be read as a photo.",
    "unsupported_file_type": "Please use a JPEG, PNG, WebP or HEIC photo.",
    "file_too_large": "That file is too large.",
}


def home(request):
    """/ : organizers to their events, everyone else to theirs."""
    if not request.user.is_authenticated:
        return redirect("login")
    if request.user.is_organizer or request.user.is_staff:
        return redirect("organizer:home")
    return redirect("participant:my_events")


def _event_or_404(code) -> Event:
    return get_object_or_404(Event.objects.select_related("owner"), code=normalize_code(code))


def _photos(event):
    return Image.objects.filter(event=event, kind=Image.Kind.PHOTO, deleted_at__isnull=True)


@login_required
def event_page(request, code, view="mine"):
    """/e/<code>/ = photos I'm in (the main path); /e/<code>/all/ = every photo, if the event allows."""
    event = _event_or_404(code)
    touch_membership(request.user, event)
    access = access_for(request.user, event)
    if access.blocked == "consent_required":
        return redirect(f"{reverse('participant:consent')}?next={reverse('participant:event', args=[event.code])}")
    if view == "all" and not access.can_browse:
        return redirect("participant:event", code=event.code)
    if view == "mine" and not access.can_match and access.can_browse:
        return redirect("participant:event_all", code=event.code)   # no face search here: all is the only view
    page, mine_count, pending = None, None, 0
    if not access.blocked:
        if access.can_match and access.matching == "ready":
            mine_ids = list(my_photos(request.user, event))
            mine_count = len(mine_ids)
        photos = _photos(event).order_by("taken_at", "seq")
        if view == "all":
            page = Paginator(photos, 200).get_page(request.GET.get("page"))
        elif mine_count:
            page = Paginator(photos.filter(pk__in=mine_ids), 200).get_page(request.GET.get("page"))
        if event.is_processing_faces:
            pending = _photos(event).filter(status__in=["pending", "processing"]).count()
    return render(request, "participant/event.html", {
        "event": event, "access": access, "view": view, "page": page, "mine_count": mine_count,
        "all_count": _photos(event).count() if access.can_browse else None, "pending": pending})


def _visible_photo(request, code, seq):
    event = _event_or_404(code)
    access = access_for(request.user, event)
    image = _photos(event).filter(seq=seq).first()
    if access.blocked or image is None or not may_see_photo(request.user, event, image, access):
        raise Http404   # never reveal whether a photo exists
    return event, image


@login_required
def photo_page(request, code, seq):
    event, image = _visible_photo(request, code, seq)
    used = DownloadLog.objects.filter(user=request.user, image=image,
                                      downloaded_at__gte=timezone.now() - timedelta(days=1)).count()
    return render(request, "participant/photo.html", {
        "event": event, "image": image,
        "downloads_left": max(0, settings.DOWNLOAD_CAP_PER_PHOTO_PER_DAY - used)})


@login_required
def photo_file(request, code, seq, rendition):
    if rendition not in ("d", "t"):
        raise Http404
    event, image = _visible_photo(request, code, seq)
    return file_response(served_relpath(image, rendition, event))


@login_required
def photo_download(request, code, seq):
    event, image = _visible_photo(request, code, seq)
    since = timezone.now() - timedelta(days=1)
    if DownloadLog.objects.filter(user=request.user, image=image, downloaded_at__gte=since).count() \
            >= settings.DOWNLOAD_CAP_PER_PHOTO_PER_DAY:
        messages.error(request, f"You have downloaded this photo {settings.DOWNLOAD_CAP_PER_PHOTO_PER_DAY} "
                                f"times in the last 24 hours. Try again tomorrow.")
        return redirect("participant:photo", code=event.code, seq=image.seq)
    DownloadLog.objects.create(user=request.user, image=image, ip=request.META.get("REMOTE_ADDR"))
    name = settings.DOWNLOAD_NAME.format(code=event.code, seq=image.seq)
    return file_response(served_relpath(image, "m", event), download_name=name)


@login_required
def my_events(request):
    rows = []
    for m in EventMember.objects.filter(user=request.user).select_related("event").order_by("-last_activity_at"):
        access = access_for(request.user, m.event)
        count = len(my_photos(request.user, m.event)) if access.can_match and access.matching == "ready" else None
        rows.append({"event": m.event, "member": m, "access": access, "count": count})
    return render(request, "participant/my_events.html", {"rows": rows})


@login_required
def my_face(request):
    if request.method == "POST":
        if not request.user.is_consent:
            return redirect(f"{reverse('participant:consent')}?next={reverse('participant:my_face')}")
        upload = request.FILES.get("file")
        slot = request.POST.get("slot", "primary")
        if upload is None:
            messages.error(request, "Choose a photo first.")
        else:
            try:
                ingest_selfie(request.user, upload.read(), slot)
                messages.success(request, "Selfie received. Checking it takes a few seconds.")
            except IngestError as e:
                messages.error(request, INGEST_MESSAGES.get(e.code, e.code))
        return redirect("participant:my_face")
    selfies = {s.slot: s for s in Image.objects.filter(owner=request.user, kind=Image.Kind.SELFIE, deleted_at__isnull=True)}
    slots = [(slot, label, selfies.get(slot)) for slot, label in Image.Slot.choices]
    return render(request, "participant/my_face.html", {"slots": slots})


@login_required
def selfie_thumb(request, image_id):
    image = get_object_or_404(Image, pk=image_id, owner=request.user, kind=Image.Kind.SELFIE, deleted_at__isnull=True)
    return file_response(image.relpath("t"))


@login_required
def consent(request):
    next_url = request.GET.get("next") or request.POST.get("next") or reverse("participant:my_events")
    if not url_has_allowed_host_and_scheme(next_url, allowed_hosts={request.get_host()}):
        next_url = reverse("participant:my_events")
    if request.method == "POST" and request.POST.get("agree") == "yes":
        if request.POST.get("version") != settings.KVKK_VERSION:   # the document changed while the page was open
            messages.error(request, "The consent text has been updated; please read the current version.")
        else:
            locale = request.POST.get("locale") if request.POST.get("locale") in settings.KVKK_LOCALES else "tr"
            kvkk.record_consent(request.user, locale, request.META.get("REMOTE_ADDR"),
                                request.META.get("HTTP_USER_AGENT", ""))
            return redirect(next_url)
    return render(request, "participant/consent.html", {"next": next_url, "kvkk": kvkk.documents()})

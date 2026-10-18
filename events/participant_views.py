"""Participant pages (Django templates). Login is phone + password until SMS sign-in exists.

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
from django.utils import timezone
from django.utils.http import url_has_allowed_host_and_scheme

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
    return Image.objects.filter(event=event, is_selfie=False, deleted_at__isnull=True)


@login_required
def event_page(request, code):
    event = _event_or_404(code)
    touch_membership(request.user, event)
    access = access_for(request.user, event)
    if access.blocked == "consent_required":
        return redirect(f"/consent/?next=/e/{event.code}/")
    mine, all_page, pending = [], None, 0
    if not access.blocked:
        if access.can_match and access.matching == "ready":
            scores = my_photos(request.user, event)
            mine = list(_photos(event).filter(pk__in=scores).order_by("taken_at", "seq"))
        if access.can_browse:
            all_page = Paginator(_photos(event).order_by("taken_at", "seq"), 200).get_page(request.GET.get("page"))
        pending = _photos(event).filter(status__in=["pending", "processing"]).count() if event.is_processing_faces else 0
    return render(request, "participant/event.html", {
        "event": event, "access": access, "mine": mine, "all_page": all_page, "pending": pending})


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
    _, image = _visible_photo(request, code, seq)
    return file_response(image.relpath(rendition))


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
    return file_response(image.relpath("m"), download_name=name)


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
            return redirect("/consent/?next=/me/face/")
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
    selfies = {s.slot: s for s in Image.objects.filter(owner=request.user, is_selfie=True, deleted_at__isnull=True)}
    slots = [(slot, label, selfies.get(slot)) for slot, label in Image.Slot.choices]
    return render(request, "participant/my_face.html", {"slots": slots})


@login_required
def selfie_thumb(request, image_id):
    image = get_object_or_404(Image, pk=image_id, owner=request.user, is_selfie=True, deleted_at__isnull=True)
    return file_response(image.relpath("t"))


@login_required
def consent(request):
    next_url = request.GET.get("next") or request.POST.get("next") or "/me/"
    if not url_has_allowed_host_and_scheme(next_url, allowed_hosts={request.get_host()}):
        next_url = "/me/"
    if request.method == "POST" and request.POST.get("agree") == "yes":
        request.user.is_consent = True
        request.user.save(update_fields=["is_consent"])
        return redirect(next_url)
    return render(request, "participant/consent.html", {"next": next_url})

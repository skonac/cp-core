"""Organizer pages (Django templates): the fallback UI that works without React.

    /o/                                   my events, balance, allowance
    /o/events/new                         create an event
    /o/events/<code>/                     event page: status, settings, upload, photo grid
    /o/events/<code>/upload               POST one file (JSON answer), used by the page's script
    /o/events/<code>/status.json          processing counts (the page polls it)
    /o/events/<code>/photos/<seq>/        one photo with its detected faces; delete
    /o/events/<code>/photos/00044_t.jpg   the files (owner only)
"""
from functools import wraps

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.core.paginator import Paginator
from django.db import transaction
from django.db.models import Count, Q
from django.http import Http404, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from media.ingest import IngestError, ingest_photo
from media.models import Face, Image
from media.storage import file_response, soft_delete

from .codes import normalize_code
from .forms import EventForm
from .models import Event


def organizer_required(view):
    @wraps(view)
    @login_required
    def wrapper(request, *args, **kwargs):
        if not (request.user.is_organizer or request.user.is_staff):
            raise PermissionDenied
        return view(request, *args, **kwargs)
    return wrapper


def _event(request, code) -> Event:
    """The event, if this user owns it (staff: any event). Anything else is a 404."""
    events = Event.objects.select_related("owner")
    if not request.user.is_staff:
        events = events.filter(owner=request.user)
    return get_object_or_404(events, code=normalize_code(code))


def _credits(user) -> dict:
    user.refresh_from_db(fields=["credit_balance", "credit_floor"])
    return {"balance": user.credit_balance,
            "allowance": max(0, user.credit_balance - user.effective_credit_floor)}


def _photos(event):
    return Image.objects.filter(event=event, kind=Image.Kind.PHOTO, deleted_at__isnull=True)


def _counts(event) -> dict:
    by_status = dict(_photos(event).values_list("status").annotate(n=Count("id")))
    counts = {s: by_status.get(s, 0) for s in Image.Status.values}
    counts["total"] = sum(by_status.values())
    counts["faces"] = Face.objects.filter(image__event=event, image__deleted_at__isnull=True).count()
    return counts


@organizer_required
def home(request):
    show_expired = request.GET.get("expired") == "1"
    events = (Event.objects.filter(owner=request.user)
              .annotate(photo_count=Count("images", filter=Q(images__kind="photo", images__deleted_at__isnull=True)))
              .order_by("-date", "-created_at"))
    events = events.filter(status=Event.Status.EXPIRED) if show_expired else events.exclude(status=Event.Status.EXPIRED)
    return render(request, "events/home.html", {"events": events, "show_expired": show_expired,
                                                "credits": _credits(request.user)})


@organizer_required
def event_new(request):
    form = EventForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        event = form.save(commit=False)
        event.owner = request.user
        event.save()
        messages.success(request, f"Event created. Code: {event.display_code}")
        return redirect("organizer:event", code=event.code)
    return render(request, "events/event_form.html", {"form": form})


@organizer_required
def event_detail(request, code):
    event = _event(request, code)
    form = EventForm(instance=event)
    if request.method == "POST":
        action = request.POST.get("action")
        if event.status == Event.Status.EXPIRED:
            messages.error(request, "This event has expired; nothing can be changed.")
        elif action in ("open", "close"):
            event.status = Event.Status.OPEN if action == "open" else Event.Status.CLOSED
            event.save(update_fields=["status", "updated_at"])
            messages.success(request, f"Event {event.get_status_display().lower()}.")
            return redirect("organizer:event", code=event.code)
        elif action == "settings":
            form = EventForm(request.POST, instance=event)
            if form.is_valid():
                form.save()
                messages.success(request, "Settings saved.")
                return redirect("organizer:event", code=event.code)
    page = Paginator(_photos(event).order_by("seq"), 200).get_page(request.GET.get("page"))
    return render(request, "events/event_detail.html", {
        "event": event, "form": form, "page": page, "counts": _counts(event),
        "credits": _credits(event.owner), "Status": Event.Status})


@organizer_required
@require_POST
def upload(request, code):
    event = _event(request, code)
    upload_file = request.FILES.get("file")
    if upload_file is None:
        return JsonResponse({"ok": False, "code": "file_required"}, status=400)
    try:
        image = ingest_photo(event, request.user, upload_file.read())
    except IngestError as e:
        return JsonResponse({"ok": False, "code": e.code, **_credits(event.owner)}, status=422)
    return JsonResponse({"ok": True, "seq": image.seq_str, **_credits(event.owner)})


@organizer_required
def status_json(request, code):
    return JsonResponse(_counts(_event(request, code)))


@organizer_required
def photo_detail(request, code, seq):
    event = _event(request, code)
    image = get_object_or_404(_photos(event), seq=seq)
    if request.method == "POST" and request.POST.get("action") == "delete":
        with transaction.atomic():
            soft_delete(image)
        messages.success(request, f"Photo {image.seq_str} deleted.")
        return redirect("organizer:event", code=event.code)
    faces = [{"left": f.bbox[0] * 100, "top": f.bbox[1] * 100,
              "width": (f.bbox[2] - f.bbox[0]) * 100, "height": (f.bbox[3] - f.bbox[1]) * 100,
              "score": f.score} for f in image.faces.all()]
    seqs = _photos(event)
    return render(request, "events/photo_detail.html", {
        "event": event, "image": image, "faces": faces,
        "prev": seqs.filter(seq__lt=seq).order_by("-seq").values_list("seq", flat=True).first(),
        "next": seqs.filter(seq__gt=seq).order_by("seq").values_list("seq", flat=True).first()})


@organizer_required
def photo_file(request, code, seq, rendition):
    if rendition not in ("m", "d", "t"):
        raise Http404
    image = get_object_or_404(_photos(_event(request, code)), seq=seq)
    return file_response(image.relpath(rendition))

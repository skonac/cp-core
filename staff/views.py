"""Staff data browser: read-only pages over everything, under /dj/staff/ (staff accounts only).

A stopgap until the admin frontend exists in cp-web. Edits stay in the Django admin (/admin/).

    /dj/staff/                       index: links to every non-API page, counters
    /dj/staff/events/                all events: owner, photo counts by status, members
    /dj/staff/events/<code>/         one event: settings, members, photo table
                                     ?as=<user_id>  only that user's matched photos, with scores
                                     ?status=failed ?deleted=1
    /dj/staff/images/<id>/           one photo or selfie: renditions, worker fields, faces, EXIF
    /dj/staff/images/<id>/m.jpg      the files (m, d, t)
    /dj/staff/users/                 all users (?q= phone / name / email)
    /dj/staff/users/<id>/            one user: profile, consent, selfies, credits, events joined and owned
    /dj/staff/worker/                workers, queue, failed images
    /dj/staff/messages/              contact form messages (?all=1 with handled); mark handled
"""
from collections import defaultdict
from datetime import timedelta
from functools import wraps

from django.conf import settings
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.core.paginator import Paginator
from django.db.models import BooleanField, Count, ExpressionWrapper, Q
from django.http import Http404, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from PIL import Image as PILImage
from pgvector.django import CosineDistance

from accounts.models import ConsentLog, ContactMessage, OtpChallenge, User
from credits.models import CreditTransaction
from shop.models import BillingProfile, Purchase
from events.codes import normalize_code
from events.models import Event, EventMember
from media.ingest import event_cover
from media.matching import my_photos, selfie_state, selfie_states
from media.models import DownloadLog, Face, Image
from media.storage import abspath, file_response
from worker.models import Worker

PAGE = 200
STATUSES = list(Image.Status.values)   # pending, processing, done, failed


def staff_required(view):
    @wraps(view)
    @login_required
    def wrapper(request, *args, **kwargs):
        if not request.user.is_staff:
            raise PermissionDenied
        return view(request, *args, **kwargs)
    return wrapper


def _photo_counts(event_ids) -> dict[int, dict]:
    """{event_id: {pending, processing, done, failed, total, deleted}} in one query."""
    counts = defaultdict(lambda: dict.fromkeys(STATUSES + ["total", "deleted"], 0))
    rows = (Image.objects.filter(event_id__in=event_ids, kind=Image.Kind.PHOTO)
            .annotate(live=ExpressionWrapper(Q(deleted_at__isnull=True), output_field=BooleanField()))
            .values_list("event_id", "status", "live").annotate(n=Count("id")))
    for event_id, status, live, n in rows:
        c = counts[event_id]
        if live:
            c[status] += n
            c["total"] += n
        else:
            c["deleted"] += n
    return counts


def _member_counts(event_ids) -> dict[int, int]:
    return dict(EventMember.objects.filter(event_id__in=event_ids)
                .values_list("event_id").annotate(n=Count("id")))


@staff_required
def index(request):
    now = timezone.now()
    stats = {
        "users": User.objects.count(),
        "organizers": User.objects.filter(is_organizer=True).count(),
        "events": Event.objects.count(),
        "photos": Image.objects.filter(kind=Image.Kind.PHOTO, deleted_at__isnull=True).count(),
        "selfies": Image.objects.filter(kind=Image.Kind.SELFIE, deleted_at__isnull=True).count(),
        "faces": Face.objects.count(),
        "queue": Image.objects.filter(status__in=[Image.Status.PENDING, Image.Status.PROCESSING],
                                      deleted_at__isnull=True).count(),
        "failed": Image.objects.filter(status=Image.Status.FAILED, deleted_at__isnull=True).count(),
        "messages": ContactMessage.objects.filter(handled_at__isnull=True).count(),
        "workers_live": Worker.objects.filter(last_seen_at__gte=now - timedelta(minutes=1)).count(),
    }
    return render(request, "staff/index.html", {"stats": stats})


@staff_required
def events(request):
    rows = list(Event.objects.select_related("owner").order_by("-created_at"))
    ids = [e.id for e in rows]
    photos, members = _photo_counts(ids), _member_counts(ids)
    for e in rows:
        e.photos, e.member_count = photos[e.id], members.get(e.id, 0)
    return render(request, "staff/events.html", {"events": rows})


@staff_required
def event(request, code):
    event = get_object_or_404(Event.objects.select_related("owner", "cover_image"), code=normalize_code(code))
    as_user = None
    scores: dict[int, float] = {}
    photos = Image.objects.filter(event=event, kind=Image.Kind.PHOTO)
    if request.GET.get("deleted") != "1":
        photos = photos.filter(deleted_at__isnull=True)
    if request.GET.get("status") in STATUSES:
        photos = photos.filter(status=request.GET["status"])
    if request.GET.get("as"):
        as_user = get_object_or_404(User, pk=request.GET["as"])
        scores = my_photos(as_user, event)
        photos = photos.filter(id__in=scores)
    page = Paginator(photos.order_by("seq"), PAGE).get_page(request.GET.get("page"))
    for img in page:
        img.score = scores.get(img.id)

    members = list(EventMember.objects.filter(event=event).select_related("user").order_by("-last_activity_at"))
    states = selfie_states([m.user_id for m in members])
    for m in members:
        m.selfie_state = states[m.user_id]
        m.matched = len(my_photos(m.user, event)) if m.selfie_state == "ready" else None
    cover = event_cover(event)
    return render(request, "staff/event.html", {
        "event": event, "counts": _photo_counts([event.id])[event.id], "page": page, "members": members,
        "as_user": as_user, "cover": cover, "statuses": STATUSES, "threshold": settings.MATCH_THRESHOLD,
        "status_filter": request.GET.get("status", ""), "show_deleted": request.GET.get("deleted") == "1"})


def _face_matches(image, faces, threshold: float, scope_all: bool) -> set[int]:
    """For each face of a photo: the people whose selfie faces are closest (best per person, up to 3),
    with cosine similarity; f.match = the best one when it reaches the threshold, f.also = the others
    that reach it too (today's matching shows the photo to all of them). Candidates: the
    event's members (scope_all: everyone with a ready selfie). Sets f.candidates, f.match."""
    selfie_faces = Face.objects.filter(image__kind=Image.Kind.SELFIE, image__status=Image.Status.DONE,
                                       image__num_faces=1, image__deleted_at__isnull=True)
    if not scope_all:
        selfie_faces = selfie_faces.filter(image__owner__memberships__event=image.event)
    rows_per_face = []
    for f in faces:
        rows = (selfie_faces.annotate(distance=CosineDistance("embedding", f.embedding))
                .order_by("distance").values_list("image__owner_id", "distance")[:6])
        best: dict[int, float] = {}
        for owner_id, distance in rows:
            best.setdefault(owner_id, 1 - distance)   # rows are nearest first: first per person is their best
        rows_per_face.append(list(best.items())[:3])
    users = User.objects.in_bulk({uid for rows in rows_per_face for uid, _ in rows})
    matched = set()
    for f, rows in zip(faces, rows_per_face):
        f.candidates = [{"user": users[uid], "sim": sim, "is_match": sim >= threshold} for uid, sim in rows]
        f.match = f.candidates[0] if f.candidates and f.candidates[0]["is_match"] else None
        f.also = [c for c in f.candidates[1:] if c["is_match"]]   # others the face would also be shown to
        if f.match:
            matched.add(f.match["user"].pk)
    return matched


def _rendition_info(image) -> list[dict]:
    out = []
    for r, label in (("t", "thumbnail"), ("d", "display"), ("m", "master")):
        if image.kind != Image.Kind.PHOTO and r == "m":
            continue   # selfies and covers keep display + thumbnail only
        path = abspath(image.relpath(r))
        info = {"r": r, "label": label, "path": image.relpath(r), "exists": path.is_file()}
        if info["exists"]:
            info["bytes"] = path.stat().st_size
            try:
                with PILImage.open(path) as im:
                    info["width"], info["height"] = im.size
            except OSError:
                pass
        out.append(info)
    return out


@staff_required
def image(request, image_id):
    image = get_object_or_404(Image.objects.select_related("event", "owner"), pk=image_id)
    faces = list(image.faces.order_by("-score"))
    for f in faces:
        f.box = {"left": f.bbox[0] * 100, "top": f.bbox[1] * 100,
                 "width": (f.bbox[2] - f.bbox[0]) * 100, "height": (f.bbox[3] - f.bbox[1]) * 100}
        f.size_pct = round((f.bbox[2] - f.bbox[0]) * 100, 1)
    try:
        threshold = float(request.GET.get("t", settings.MATCH_THRESHOLD))
    except ValueError:
        threshold = settings.MATCH_THRESHOLD
    scope_all = request.GET.get("scope") == "all"
    matched_users = set()
    if image.kind == Image.Kind.PHOTO and faces:
        matched_users = _face_matches(image, faces, threshold, scope_all)
    neighbours = {}
    if image.kind == Image.Kind.PHOTO:
        siblings = Image.objects.filter(event=image.event, kind=Image.Kind.PHOTO)
        neighbours = {
            "prev": siblings.filter(seq__lt=image.seq).order_by("-seq").values_list("id", flat=True).first(),
            "next": siblings.filter(seq__gt=image.seq).order_by("seq").values_list("id", flat=True).first()}
    return render(request, "staff/image.html", {
        "image": image, "faces": faces, "renditions": _rendition_info(image),
        "threshold": threshold, "default_threshold": settings.MATCH_THRESHOLD, "scope_all": scope_all,
        "matched_users": len(matched_users), "matched_faces": sum(1 for f in faces if getattr(f, "match", None)),
        "exif": sorted((image.exif or {}).items()), **neighbours,
        "downloads": image.downloads.select_related("user").order_by("-downloaded_at")[:20]})


@staff_required
def image_file(request, image_id, rendition):
    if rendition not in ("m", "d", "t"):
        raise Http404
    image = get_object_or_404(Image, pk=image_id)
    return file_response(image.relpath(rendition))


@staff_required
def users(request):
    rows = User.objects.order_by("-created_at")
    q = request.GET.get("q", "").strip()
    if q:
        rows = rows.filter(Q(phone__icontains=q) | Q(full_name__icontains=q) | Q(email__icontains=q))
    page = Paginator(rows, PAGE).get_page(request.GET.get("page"))
    ids = [u.id for u in page]
    joined = dict(EventMember.objects.filter(user_id__in=ids).values_list("user_id").annotate(n=Count("id")))
    owned = dict(Event.objects.filter(owner_id__in=ids).values_list("owner_id").annotate(n=Count("id")))
    states = selfie_states(ids)
    for u in page:
        u.joined, u.owned, u.selfie_state = joined.get(u.id, 0), owned.get(u.id, 0), states[u.id]
    return render(request, "staff/users.html", {"page": page, "q": q, "kvkk_version": settings.KVKK_VERSION})


@staff_required
def user(request, user_id):
    u = get_object_or_404(User, pk=user_id)
    memberships = list(EventMember.objects.filter(user=u).select_related("event").order_by("-last_activity_at"))
    owned = list(Event.objects.filter(owner=u).order_by("-created_at"))
    counts = _photo_counts([m.event_id for m in memberships] + [e.id for e in owned])
    state = selfie_state(u)
    for m in memberships:
        m.photos = counts[m.event_id]
        m.matched = len(my_photos(u, m.event)) if state == "ready" else None
    members = _member_counts([e.id for e in owned])
    for e in owned:
        e.photos, e.member_count = counts[e.id], members.get(e.id, 0)
    return render(request, "staff/user.html", {
        "u": u, "selfie_state": state, "memberships": memberships, "owned": owned,
        "selfies": Image.objects.filter(owner=u, kind=Image.Kind.SELFIE).order_by("deleted_at", "slot", "-created_at"),
        "consents": ConsentLog.objects.filter(user=u).order_by("-created_at"),
        "purchases": Purchase.objects.filter(user=u).order_by("-created_at")[:50],
        "billing": BillingProfile.objects.filter(user=u).first(),
        "credits": CreditTransaction.objects.filter(user=u).select_related("actor").order_by("-created_at")[:50],
        "uploads": Image.objects.filter(owner=u, kind=Image.Kind.PHOTO, deleted_at__isnull=True).count(),
        "downloads": DownloadLog.objects.filter(user=u).select_related("image__event").order_by("-downloaded_at")[:20],
        "download_total": DownloadLog.objects.filter(user=u).count(),
        "otps": OtpChallenge.objects.filter(phone=u.phone).select_related("event").order_by("-created_at")[:10],
        "kvkk_version": settings.KVKK_VERSION})


@staff_required
def worker(request):
    now = timezone.now()
    workers = list(Worker.objects.order_by("-last_seen_at"))
    for w in workers:
        w.silent_s = int((now - w.last_seen_at).total_seconds())
    live = Image.objects.filter(deleted_at__isnull=True)
    unfinished = live.filter(status__in=[Image.Status.PENDING, Image.Status.PROCESSING])
    queue_events = (unfinished.filter(kind=Image.Kind.PHOTO).values("event__code", "event__name", "event__status",
                                                              "event__is_processing_faces")
                    .annotate(pending=Count("id", filter=Q(status=Image.Status.PENDING)),
                              processing=Count("id", filter=Q(status=Image.Status.PROCESSING)))
                    .order_by("event__code"))
    return render(request, "staff/worker.html", {
        "workers": workers, "queue_events": queue_events,
        "selfies_waiting": unfinished.filter(kind=Image.Kind.SELFIE).count(),
        "processing": unfinished.filter(status=Image.Status.PROCESSING).select_related("event", "owner")
                                .order_by("processing_until")[:50],
        "failed": live.filter(status=Image.Status.FAILED).select_related("event", "owner").order_by("-created_at")[:200],
        "had_errors": live.filter(status=Image.Status.DONE).exclude(error="").select_related("event", "owner")
                          .order_by("-processed_at")[:50],
        "lease_s": settings.WORKER_LEASE})


@staff_required
def purchase_documents(request, purchase_id: int):
    """The ÖBF + MSS page stored with a purchase (shop/documents.py), as the buyer accepted it."""
    purchase = get_object_or_404(Purchase.objects.exclude(docs_html=""), pk=purchase_id)
    response = HttpResponse(purchase.docs_html, content_type="text/html; charset=utf-8")
    response["Cache-Control"] = "private, no-store"
    return response


@staff_required
def messages_page(request):
    """Contact form messages, newest first; ?all=1 includes handled ones. POST handle/unhandle."""
    if request.method == "POST":
        msg = get_object_or_404(ContactMessage, pk=request.POST.get("id"))
        if request.POST.get("action") == "handle":
            msg.handled_at, msg.handled_by = timezone.now(), request.user
        else:
            msg.handled_at, msg.handled_by = None, None
        msg.save(update_fields=["handled_at", "handled_by"])
        return redirect(request.get_full_path())
    show_all = request.GET.get("all") == "1"
    rows = ContactMessage.objects.select_related("user", "handled_by").order_by("-created_at")
    if not show_all:
        rows = rows.filter(handled_at__isnull=True)
    page = Paginator(rows, PAGE).get_page(request.GET.get("page"))
    return render(request, "staff/messages.html", {"page": page, "show_all": show_all})

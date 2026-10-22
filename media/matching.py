"""Matching, computed on read (design/core.md, "Matching"). Nothing is stored.

A user's faces are the faces of their ready selfies (done, exactly one face). A face in a photo
is theirs when it is at least MATCH_THRESHOLD similar to one of their faces AND more similar to
them than to every face they rejected ("not me", FaceRejection) plus MATCH_REJECT_MARGIN.
Rejected faces work as negative examples: a rejected face is hidden itself (similarity 1 to
itself), and so are other faces of that person (much closer to the rejected face than to the
user), while the user's own faces stay (closer to their selfie). Rejections count across events.
A photo is theirs when it has at least one such face; the best one is its "matched face".
"""
from dataclasses import dataclass

from django.conf import settings
from django.db.models import OuterRef, Subquery
from pgvector.django import CosineDistance

from .models import Face, FaceRejection, Image


@dataclass
class Match:
    face_id: int
    score: float             # cosine similarity to the user's closest selfie face
    bbox: list[float]        # normalized 0-1, x1 y1 x2 y2
    landmarks: list[float]   # normalized, 5 x/y pairs

    def json(self) -> dict:
        return {"faceId": str(self.face_id), "bbox": [round(v, 5) for v in self.bbox],
                "landmarks": [[round(self.landmarks[i], 5), round(self.landmarks[i + 1], 5)]
                              for i in range(0, len(self.landmarks), 2)],
                "score": round(self.score, 3)}


def user_faces(user):
    return Face.objects.filter(image__owner=user, image__kind=Image.Kind.SELFIE, image__status=Image.Status.DONE,
                               image__num_faces=1, image__deleted_at__isnull=True)


def _state(selfies) -> str:
    if not selfies:
        return "no_selfie"
    if any(status == Image.Status.DONE and n == 1 for status, n in selfies):
        return "ready"
    if any(status in (Image.Status.PENDING, Image.Status.PROCESSING) for status, _ in selfies):
        return "selfie_processing"
    return "selfie_rejected"


def selfie_state(user) -> str:
    """no_selfie | selfie_processing | selfie_rejected | ready"""
    return _state(list(Image.objects.filter(owner=user, kind=Image.Kind.SELFIE, deleted_at__isnull=True)
                       .values_list("status", "num_faces")))


def selfie_states(user_ids) -> dict[int, str]:
    """selfie_state() for many users in one query."""
    rows: dict[int, list] = {uid: [] for uid in user_ids}
    for owner_id, status, n in (Image.objects.filter(owner_id__in=list(rows), kind=Image.Kind.SELFIE,
                                                     deleted_at__isnull=True)
                                .values_list("owner_id", "status", "num_faces")):
        rows[owner_id].append((status, n))
    return {uid: _state(selfies) for uid, selfies in rows.items()}


def _matches(user, faces, threshold: float | None = None) -> dict[int, Match]:
    """{image_id: best Match} among `faces` (a Face queryset). One query per selfie face (max 2);
    the rejection test is a subquery evaluated only for faces above the threshold."""
    threshold = settings.MATCH_THRESHOLD if threshold is None else threshold
    rejected = FaceRejection.objects.filter(user=user)
    closest_rejected = Subquery(rejected.annotate(d=CosineDistance("face__embedding", OuterRef("embedding")))
                                .order_by("d").values("d")[:1])
    has_rejections = rejected.exists()
    best_face: dict[int, tuple[float, float | None, int, list, list]] = {}   # face id -> (score, neg, image, bbox, lm)
    for embedding in user_faces(user).values_list("embedding", flat=True):
        rows = (faces.annotate(distance=CosineDistance("embedding", embedding))   # 1 - cosine similarity
                .filter(distance__lte=1 - threshold))
        if has_rejections:
            rows = rows.annotate(neg_distance=closest_rejected)
        fields = ["id", "image_id", "bbox", "landmarks", "distance"] + (["neg_distance"] if has_rejections else [])
        for face_id, image_id, bbox, landmarks, distance, *neg in rows.values_list(*fields):
            score = 1 - distance
            if face_id not in best_face or score > best_face[face_id][0]:
                best_face[face_id] = (score, neg[0] if neg else None, image_id, bbox, landmarks)
    best: dict[int, Match] = {}
    for face_id, (score, neg_distance, image_id, bbox, landmarks) in best_face.items():
        if neg_distance is not None and score <= (1 - neg_distance) + settings.MATCH_REJECT_MARGIN:
            continue   # closer to a face the user said is not them
        if image_id not in best or score > best[image_id].score:
            best[image_id] = Match(face_id, score, bbox, landmarks)
    return best


def _event_faces(event):
    return Face.objects.filter(image__event=event, image__kind=Image.Kind.PHOTO, image__deleted_at__isnull=True)


def my_matches(user, event, threshold: float | None = None) -> dict[int, Match]:
    """{image_id: Match} for this user's photos in this event, with the face that matched."""
    return _matches(user, _event_faces(event), threshold)


def my_photos(user, event, threshold: float | None = None) -> dict[int, float]:
    """{image_id: best similarity} for this user's photos in this event."""
    return {image_id: m.score for image_id, m in my_matches(user, event, threshold).items()}


def photo_match(user, image) -> Match | None:
    """The same test for one photo (its few faces only): cheap enough for every file request."""
    return _matches(user, image.faces.all()).get(image.pk)


def is_my_photo(user, image) -> bool:
    return photo_match(user, image) is not None


def similarity_to_user(user, face) -> float | None:
    """Similarity of a face to the user's closest selfie face (no threshold); None without a ready selfie."""
    sims = [1 - Face.objects.filter(pk=face.pk).annotate(d=CosineDistance("embedding", e)).values_list("d", flat=True)[0]
            for e in user_faces(user).values_list("embedding", flat=True)]
    return round(max(sims), 4) if sims else None

"""Matching, computed on read (design/core.md, "Matching"). Nothing is stored.

A user's faces are the faces of their ready selfies (done, exactly one face). A photo is
"theirs" when any face in it is at least MATCH_THRESHOLD similar to any of their faces.
"""
from django.conf import settings
from pgvector.django import CosineDistance

from .models import Face, Image


def user_faces(user):
    return Face.objects.filter(image__owner=user, image__is_selfie=True, image__status=Image.Status.DONE,
                               image__num_faces=1, image__deleted_at__isnull=True)


def my_photos(user, event, threshold: float | None = None) -> dict[int, float]:
    """{image_id: best similarity} for this user's photos in this event. One query per selfie (max 2)."""
    threshold = settings.MATCH_THRESHOLD if threshold is None else threshold
    best: dict[int, float] = {}
    for embedding in user_faces(user).values_list("embedding", flat=True):
        rows = (Face.objects
                .filter(image__event=event, image__is_selfie=False, image__deleted_at__isnull=True)
                .annotate(distance=CosineDistance("embedding", embedding))   # 1 - cosine similarity
                .filter(distance__lte=1 - threshold)
                .values_list("image_id", "distance"))
        for image_id, distance in rows:
            best[image_id] = max(best.get(image_id, 0.0), 1 - distance)
    return best

"""Response shapes of the events API, for the docs and the contract test (see common.api.Out).
The JSON itself is built in events/api.py; docs/api.md explains the fields."""
from typing import Literal

from pydantic import Field

from accounts.schemas import Matching
from common.api import Out, envelope


class PublicEventOut(Out):
    code: str = Field(examples=["RK238"])
    name: str = Field(examples=["Derya & Can"])
    type: Literal["wedding", "sports", "graduation", "corporate", "holiday", "vacation", "other"]
    date: str = Field(examples=["2026-06-12"])
    locationLabel: str | None = Field(examples=["İstanbul"])
    status: Literal["active"] = Field(description="Closed/expired events answer 423/410 instead.")
    hasFaceSearch: bool
    allowsBrowseAll: bool
    requiresConsent: bool


class MatchedFaceOut(Out):
    faceId: str = Field(description="Send it with \"not me\" (face-rejections).", examples=["5121"])
    bbox: list[float] = Field(description="x1, y1, x2, y2, normalized 0–1 (fractions of the image's width and "
                                          "height; the same for thumbnail, display and download).",
                              examples=[[0.41234, 0.18012, 0.52871, 0.37455]])
    landmarks: list[list[float]] = Field(description="5 points [x, y], normalized: the two eyes, the nose tip, "
                                                     "the two mouth corners (detector order).",
                                         examples=[[[0.44, 0.25], [0.5, 0.25], [0.47, 0.29], [0.45, 0.33], [0.5, 0.33]]])
    score: float = Field(description="Cosine similarity to the user's selfie (threshold 0.363).", examples=[0.612])


class PhotoOut(Out):
    id: str = Field(description="Photo number in its event; unique only within the event.", examples=["00017"])
    width: int = Field(description="Display image pixels.", examples=[1600])
    height: int = Field(examples=[1200])
    takenAt: str | None = Field(examples=["2026-06-12T18:14:00+00:00"])
    thumbnailUrl: str = Field(examples=["/api/events/rk238/photos/00017/thumb.jpg"])
    displayUrl: str = Field(examples=["/api/events/rk238/photos/00017/display.jpg"])
    downloadUrl: str = Field(examples=["/api/events/rk238/photos/00017/download"])
    matchedFace: MatchedFaceOut | None = Field(description="The user's face in this photo, the reason it is in "
                                                           "\"mine\"; null when it doesn't match (or no face search).")


class GalleryOut(Out):
    eventCode: str = Field(examples=["RK238"])
    view: Literal["mine", "all"]
    matching: Matching | None = Field(description="State of 'photos of me'; explains an empty mine list.",
                                      examples=["ready"])
    canBrowseAll: bool = Field(description="Show the 'All photos' tab.", examples=[False])
    photosTotal: int = Field(description="All photos of the event.", examples=[3000])
    photosPending: int = Field(description="Still being processed; re-fetch later when > 0.", examples=[120])
    total: int = Field(description="Photos in this view.", examples=[24])
    photos: list[PhotoOut]
    nextCursor: int | None = Field(description="Offset of the next page; null on the last page.", examples=[None])


class GalleryPhotoOut(Out):
    eventCode: str = Field(examples=["RK238"])
    view: Literal["mine", "all"]
    photo: PhotoOut
    position: int = Field(description="1-based, within the view.", examples=[4])
    total: int = Field(examples=[24])
    previousPhotoId: str | None = Field(examples=["00012"])
    nextPhotoId: str | None = Field(examples=["00031"])
    downloadsLeftToday: int = Field(description="Hide the download button at 0.", examples=[3])


class FaceRejectionOut(Out):
    photo: PhotoOut


PublicEventResponse = envelope("PublicEventResponse", PublicEventOut, "event_retrieved")
GalleryResponse = envelope("GalleryResponse", GalleryOut, "gallery_retrieved")
GalleryPhotoResponse = envelope("GalleryPhotoResponse", GalleryPhotoOut, "gallery_photo_retrieved")
FaceRejectionResponse = envelope("FaceRejectionResponse", FaceRejectionOut, "face_rejected")
FaceRejectionRemovedResponse = envelope("FaceRejectionRemovedResponse", FaceRejectionOut, "face_rejection_removed")

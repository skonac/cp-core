"""Response shapes of the organizer API (/api/organizer/…), for the docs and the contract test.
The JSON is built in events/organizer_api.py; docs/api.md §3.6 explains the fields."""
from typing import Literal

from pydantic import Field

from accounts.schemas import UploaderAccessOut
from common.api import Out, envelope

EventType = Literal["wedding", "sports", "graduation", "corporate", "other"]
EventStatus = Literal["active", "access_closed", "expired"]


class OrganizerEventOut(Out):
    id: str = Field(description="Same as code; use either in paths.", examples=["RK238"])
    code: str = Field(examples=["RK238"])
    name: str = Field(examples=["Derya & Can"])
    type: EventType
    date: str = Field(examples=["2026-06-12"])
    city: str | None = Field(examples=["İstanbul"])
    venue: str | None = Field(examples=["Çırağan Sarayı"])
    status: EventStatus = Field(description="active = open; access_closed = participants locked out; expired = final.")
    hasFaceSearch: bool = Field(description="Photos go to face matching ('photos of me').")
    allowsBrowseAll: bool = Field(description="Participants may browse all photos.")
    requiresConsent: bool = Field(description="Participants need KVKK consent to enter.")
    photoCount: int = Field(examples=[684])
    processedPhotoCount: int = Field(description="Face processing finished (done).", examples=[680])
    pendingPhotoCount: int = Field(description="Waiting for or in face processing.", examples=[4])
    failedPhotoCount: int = Field(examples=[0])
    participantCount: int = Field(examples=[213])
    coverThumbnailUrl: str | None = Field(description="Uploaded cover, else the first photo; null when neither.",
                                          examples=["/api/organizer/events/RK238/cover/thumb.jpg"])
    coverIsUploaded: bool = Field(description="False: the cover shown is the first photo.")
    frameLandscapeUrl: str | None = Field(description="That slot's frame PNG; null when empty.",
                                          examples=["/api/organizer/events/RK238/frame/landscape.png"])
    framePortraitUrl: str | None = Field(description="That slot's frame PNG; null when empty.",
                                         examples=["/api/organizer/events/RK238/frame/portrait.png"])
    joinUrl: str | None = Field(description="Link for participants (QR content); null in the uploader API.",
                                examples=["https://cleverphotos.org/?event=RK238"])
    createdAt: str = Field(examples=["2026-10-19T08:30:00+00:00"])


class EventDataOut(Out):
    event: OrganizerEventOut


class DashboardOrganizerOut(Out):
    displayName: str | None = Field(examples=["Seko Konac"])
    creditBalance: int = Field(description="May be negative (overdraft).", examples=[1840])


class DashboardTotalsOut(Out):
    activeEvents: int
    totalPhotos: int
    totalParticipants: int


class DashboardOut(Out):
    organizer: DashboardOrganizerOut
    totals: DashboardTotalsOut
    events: list[OrganizerEventOut] = Field(description="Not expired, newest first.")
    recentActivity: list[dict] = Field(description="Always empty in this version.", examples=[[]])


class EventListOut(Out):
    events: list[OrganizerEventOut]
    total: int


class UploadCheckOut(Out):
    eligible: bool
    requiredCredits: int = Field(examples=[240])
    creditBalance: int = Field(examples=[180])
    remainingCredits: int | None = Field(description="Balance after the upload, when eligible.", examples=[None])
    missingCredits: int = Field(description="Credits to buy first; 0 when eligible.", examples=[60])


class UploadResultOut(Out):
    clientId: str = Field(examples=["local-1"])
    photoId: str | None = Field(description="Accepted: the photo number.", examples=["00045"])
    fileName: str | None = Field(description="Accepted: our name for it (never the uploaded name).", examples=["00045.jpg"])
    status: Literal["queued", "ready", "rejected"] = Field(description="queued = waiting for face processing.")
    errorCode: str | None = Field(examples=[None])


class UploadBatchOut(Out):
    acceptedCount: int
    rejectedCount: int
    creditBalance: int
    results: list[UploadResultOut]


PhotoStatus = Literal["queued", "processing", "ready", "failed"]


class OrganizerPhotoOut(Out):
    id: str = Field(examples=["00017"])
    fileName: str = Field(examples=["00017.jpg"])
    status: PhotoStatus
    faceCount: int | None = Field(description="Faces found; null until processed.", examples=[3])
    thumbnailUrl: str = Field(examples=["/api/organizer/events/RK238/photos/00017/thumb.jpg"])
    displayUrl: str = Field(examples=["/api/organizer/events/RK238/photos/00017/display.jpg"])
    width: int = Field(description="Display image pixels.", examples=[1600])
    height: int = Field(examples=[1200])
    takenAt: str | None = Field(examples=["2026-06-12T18:14:00+00:00"])
    uploadedAt: str = Field(examples=["2026-10-18T18:30:00+00:00"])
    errorCode: str | None = Field(examples=[None])
    uploadedBy: str | None = Field(description="The uploader's name when an uploader uploaded it; "
                                               "null = the organizer.", examples=[None])


class PhotoCountsOut(Out):
    queued: int
    processing: int
    ready: int
    failed: int


class PhotoListOut(Out):
    photos: list[OrganizerPhotoOut]
    total: int = Field(description="Photos matching the status filter.")
    counts: PhotoCountsOut = Field(description="All photos of the event, by status.")
    nextCursor: int | None


class PhotoMutationOut(Out):
    affectedCount: int
    creditBalance: int


class UploaderEventOut(Out):
    code: str = Field(examples=["RK238"])
    name: str = Field(examples=["Derya & Can"])
    status: EventStatus = Field(description="An expired event no longer counts for the uploader.")


class UploaderOut(Out):
    id: str = Field(examples=["12"])
    fullName: str = Field(examples=["Mert Yılmaz"])
    phoneNumber: str = Field(examples=["+905550000001"])
    status: Literal["active", "revoked"]
    events: list[UploaderEventOut] = Field(description="The events it may upload to (by date).")
    createdAt: str = Field(examples=["2026-10-20T09:00:00+00:00"])
    lastLoginAt: str | None = Field(examples=[None])


class UploaderListOut(Out):
    uploaders: list[UploaderOut]
    total: int


class UploaderDataOut(Out):
    uploader: UploaderOut


class UploaderAccessesOut(Out):
    accesses: list[UploaderAccessOut]


class ParticipantOut(Out):
    membershipId: str = Field(examples=["123"])
    displayName: str | None = Field(examples=["Ece Yılmaz"])
    maskedPhone: str = Field(examples=["•••••••••4218"])
    joinedAt: str
    lastActiveAt: str
    hasConsent: bool
    matchStatus: Literal["consent_pending", "selfie_pending", "processing", "completed", "failed"]
    matchedPhotoCount: int | None = Field(description="null unless matchStatus is completed.", examples=[18])


class ParticipantCountsOut(Out):
    completed: int
    pending: int = Field(description="consent_pending + selfie_pending + processing")
    failed: int


class ParticipantListOut(Out):
    participants: list[ParticipantOut]
    total: int
    counts: ParticipantCountsOut


DashboardResponse = envelope("OrganizerDashboardResponse", DashboardOut, "organizer_dashboard_retrieved")
EventListResponse = envelope("OrganizerEventListResponse", EventListOut, "organizer_events_retrieved")
EventCreatedResponse = envelope("OrganizerEventCreatedResponse", EventDataOut, "organizer_event_created")
EventResponse = envelope("OrganizerEventResponse", EventDataOut, "organizer_event_retrieved")
EventUpdatedResponse = envelope("OrganizerEventUpdatedResponse", EventDataOut, "organizer_event_updated")
CoverResponse = envelope("OrganizerCoverResponse", EventDataOut, "organizer_cover_updated")
FrameResponse = envelope("OrganizerFrameResponse", EventDataOut, "organizer_frame_updated")
UploadCheckResponse = envelope("UploadCheckResponse", UploadCheckOut, "photo_upload_eligibility_checked")
UploadBatchResponse = envelope("UploadBatchResponse", UploadBatchOut, "organizer_photos_uploaded")
PhotoListResponse = envelope("OrganizerPhotoListResponse", PhotoListOut, "organizer_photos_retrieved")
PhotosDeletedResponse = envelope("OrganizerPhotosDeletedResponse", PhotoMutationOut, "organizer_photos_deleted")
ParticipantListResponse = envelope("ParticipantListResponse", ParticipantListOut, "organizer_participants_retrieved")
UploaderListResponse = envelope("UploaderListResponse", UploaderListOut, "organizer_uploaders_retrieved")
UploaderResponse = envelope("UploaderResponse", UploaderDataOut, "organizer_uploader_created")
UploaderUpdatedResponse = envelope("UploaderUpdatedResponse", UploaderDataOut, "organizer_uploader_updated")
UploaderRevokedResponse = envelope("UploaderRevokedResponse", UploaderDataOut, "organizer_uploader_revoked")

# The uploader API (events/uploader_api.py): same shapes, own codes.
UpAccessesResponse = envelope("UploaderAccessesResponse", UploaderAccessesOut, "uploader_accesses_retrieved")
UpEventListResponse = envelope("UploaderEventListResponse", EventListOut, "uploader_events_retrieved")
UpEventResponse = envelope("UploaderEventResponse", EventDataOut, "uploader_event_retrieved")
UpUploadBatchResponse = envelope("UploaderUploadBatchResponse", UploadBatchOut, "uploader_photos_uploaded")
UpPhotoListResponse = envelope("UploaderPhotoListResponse", PhotoListOut, "uploader_photos_retrieved")
UpPhotosDeletedResponse = envelope("UploaderPhotosDeletedResponse", PhotoMutationOut, "uploader_photos_deleted")

"""Response shapes of the auth/me API, for the docs and the contract test (see common.api.Out).
The JSON itself is built in accounts/api.py; docs/api.md explains the fields."""
from typing import Literal

from pydantic import Field

from common.api import Out, envelope

Role = Literal["participant", "organizer", "photographer", "admin"]
SelfieState = Literal["no_selfie", "selfie_processing", "selfie_rejected", "ready"]
Matching = Literal["consent_required", "matching_off", "no_selfie", "selfie_processing", "selfie_rejected", "ready"]


class KvkkDocumentsOut(Out):
    version: str = Field(description="Current KVKK document version.", examples=["002"])
    urls: dict[Literal["tr", "en"], str] = Field(
        description="HTML page per language.",
        examples=[{"tr": "/api/kvkk/kvkk_tr_002.html", "en": "/api/kvkk/kvkk_en_002.html"}])


class UserOut(Out):
    id: str = Field(examples=["69"])
    phoneNumber: str = Field(examples=["+905551234567"])
    fullName: str | None = Field(examples=["Seko Konac"])
    email: str | None = Field(examples=[None])
    profileCompleted: bool = Field(description="true when fullName is set.")
    accountRoles: list[Role] = Field(description="UI visibility only; the server checks everything.",
                                     examples=[["participant"]])
    hasConsent: bool = Field(description="Consent given to the CURRENT KVKK version (kvkk.version).")
    consentVersion: str | None = Field(description="Version last consented to; null = never.", examples=[None])
    kvkk: KvkkDocumentsOut
    selfieState: SelfieState = Field(examples=["no_selfie"])
    selfieThumbnailUrl: str | None = Field(description="Primary selfie's thumbnail.", examples=[None])


class OtpChallengeOut(Out):
    challengeId: str = Field(examples=["otp_WcM7QQ-RIA9d2osCR_Mx6A"])
    maskedPhone: str = Field(examples=["•••••••••4567"])
    expiresInSeconds: int = Field(examples=[120])
    resendAfterSeconds: int = Field(examples=[30])


class MembershipOut(Out):
    eventCode: str = Field(examples=["RK238"])
    role: Literal["participant"]


class UploaderAccessOut(Out):
    id: str = Field(description="accessId in /api/uploader/accesses/{accessId}/…", examples=["12"])
    organizerName: str = Field(examples=["Futuruniq"])
    uploaderName: str = Field(description="The name the organizer gave this uploader.",
                                  examples=["Mert Yılmaz"])


class SignInOut(Out):
    user: UserOut = Field(description="For an uploader without an account: id is empty, no roles.")
    nextStep: Literal["consent", "selfie", "gallery", "organizer_home", "account_home", "uploader_home"] = Field(
        description="consent/selfie/gallery: signed in from an event link; organizer_home/account_home: no event; "
                    "uploader_home: organizer entry by a phone an organizer added as uploader (§3.10).")
    uploaderAccesses: list[UploaderAccessOut] = Field(
        description="Organizers this phone may upload for (empty for most people).", examples=[[]])
    isNewUser: bool
    eventCode: str | None = Field(examples=["RK238"])
    eventMembership: MembershipOut | None


class ConsentOut(Out):
    hasConsent: Literal[True]
    version: str = Field(examples=["002"])


class MyEventOut(Out):
    code: str = Field(examples=["RK238"])
    name: str = Field(examples=["Derya & Can"])
    type: Literal["wedding", "sports", "graduation", "corporate", "other"]
    date: str = Field(examples=["2026-06-12"])
    locationLabel: str | None = Field(examples=["İstanbul"])
    status: Literal["active", "access_closed", "expired"]
    matchedPhotoCount: int = Field(examples=[24])
    matching: Matching | None = Field(examples=["ready"])
    coverThumbnailUrl: None = Field(description="Always null for now.")
    lastActivityAt: str = Field(examples=["2026-10-11T08:30:00+00:00"])
    membershipRole: Literal["participant"]


class MyEventsOut(Out):
    events: list[MyEventOut]
    total: int


class SelfieOut(Out):
    id: str = Field(examples=["68"])
    slot: Literal["primary", "secondary"]
    status: Literal["processing", "ready", "rejected"] = Field(description="Poll GET /api/me/selfies until not processing.",
                                                               examples=["ready"])
    reason: Literal["no_face", "multiple_faces", "unreadable"] | None = Field(description="Set when rejected.",
                                                                              examples=[None])
    thumbnailUrl: str = Field(examples=["/api/me/selfies/68/thumb.jpg"])
    createdAt: str = Field(examples=["2026-10-19T08:30:00+00:00"])


class SelfiesOut(Out):
    selfies: list[SelfieOut]
    selfieState: SelfieState = Field(examples=["ready"])


CsrfResponse = envelope("CsrfResponse", None, "csrf_set")
OtpSentResponse = envelope("OtpSentResponse", OtpChallengeOut, "otp_sent")
SignInResponse = envelope("SignInResponse", SignInOut, "phone_verified")
ProfileResponse = envelope("ProfileResponse", UserOut, "profile_retrieved")
ProfileUpdatedResponse = envelope("ProfileUpdatedResponse", UserOut, "profile_updated")
ConsentResponse = envelope("ConsentResponse", ConsentOut, "consent_recorded")
MyEventsResponse = envelope("MyEventsResponse", MyEventsOut, "user_events_retrieved")
SelfiesResponse = envelope("SelfiesResponse", SelfiesOut, "selfies_retrieved")
SelfieAcceptedResponse = envelope("SelfieAcceptedResponse", SelfieOut, "selfie_accepted")


class ContactReceivedOut(Out):
    messageId: str = Field(examples=["42"])


ContactReceivedResponse = envelope("ContactReceivedResponse", ContactReceivedOut, "contact_message_received")

"""API contract test: every endpoint, called the way cp-web calls it, and every JSON response
checked against the schema the endpoint declares (what /api/docs shows). common.testing.ApiClient
does the checking; this file drives one realistic guest flow plus the error cases.
The last check fails when an endpoint exists that this test never called."""
import io
import re
import zipfile
from datetime import timedelta

from django.conf import settings
from django.core.cache import cache
from django.test import TestCase
from django.urls import resolve
from django.utils import timezone

from accounts.models import OtpChallenge, User
from config.api import api
from events.models import Event
from media.models import Image

from .testing import ApiClient, TempMedia, add_photo, face_of, make_event, person, process, jpeg

ME, STRANGER = person(1), person(2)


def _norm(route: str) -> str:
    return re.sub(r"<(?:\w+:)?\w+>|\{\w+\}", "{}", route.strip("/"))


class ApiContractTests(TempMedia, TestCase):
    def setUp(self):
        cache.clear()
        self.owner = User.objects.create_user("+905550000001", is_organizer=True, credit_balance=100,
                                              email="owner@example.com")
        self.event = make_event(self.owner, name="Derya & Can", city="İstanbul")
        self.p_me = add_photo(self.event, 1, [face_of(ME, 1), face_of(STRANGER, 2)])
        self.p_other = add_photo(self.event, 2, [face_of(STRANGER, 3)])
        self.p_me2 = add_photo(self.event, 3, [face_of(ME, 4)])
        self.p_pending = add_photo(self.event, 4)                        # not processed yet
        self.closed = make_event(self.owner, status=Event.Status.CLOSED)
        self.expired = make_event(self.owner, status=Event.Status.EXPIRED)
        self.c = ApiClient()

    def assertCode(self, r, status, code):
        self.assertEqual((r.status_code, r.json().get("code")), (status, code), r.content[:300])

    def test_guest_flow_and_contract(self):
        c, code = self.c, self.event.display_code
        ev = f"/api/events/{code}"

        # --- before sign-in
        self.assertCode(c.get("/api/health"), 200, "ok")
        self.assertCode(c.get("/api/me"), 401, "authentication_required")
        self.assertCode(c.get("/api/auth/csrf"), 200, "csrf_set")
        r = c.get(f"/api/public/events/{code.lower()}")
        self.assertCode(r, 200, "event_retrieved")
        self.assertEqual(r.json()["data"]["code"], code)
        self.assertCode(c.get(f"/api/public/events/{self.closed.code}"), 423, "event_access_closed")
        self.assertCode(c.get(f"/api/public/events/{self.expired.code}"), 410, "event_expired")
        self.assertCode(c.get("/api/public/events/zzzzz"), 404, "event_not_found")
        self.assertCode(c.post_json("/api/auth/otp-requests", {"phoneNumber": "123"}), 422, "invalid_phone_number")
        self.assertCode(c.post_json("/api/auth/otp-requests", {"phoneNumber": "+905550000002", "eventCode": "zzzzz"}),
                        404, "event_not_found")
        self.assertCode(c.post_json("/api/auth/otp-requests", {}), 422, "validation_error")
        self.assertEqual(c.get("/api/kvkk/" + f"kvkk_tr_{settings.KVKK_VERSION}.html")["Content-Type"],
                         "text/html; charset=utf-8")
        self.assertEqual(c.head(f"/api/kvkk/kvkk_en_{settings.KVKK_VERSION}.html").status_code, 200)
        self.assertCode(c.get("/api/kvkk/kvkk_xx_001.html"), 404, "not_found")

        # --- OTP errors
        r = c.post_json("/api/auth/otp-requests", {"phoneNumber": "+905550000003"})
        challenge = r.json()["data"]["challengeId"]
        self.assertCode(c.post_json("/api/auth/otp-verifications", {"challengeId": challenge, "code": "000000"}),
                        422, "invalid_otp")
        OtpChallenge.objects.filter(pk=challenge).update(expires_at=timezone.now() - timedelta(seconds=1))
        ch = OtpChallenge.objects.get(pk=challenge)
        self.assertCode(c.post_json("/api/auth/otp-verifications", {"challengeId": ch.pk, "code": ch.dev_code}),
                        410, "otp_expired")
        for _ in range(2):   # 3 codes per phone per 15 min (the 30 s gap applies too)
            c.post_json("/api/auth/otp-requests", {"phoneNumber": "+905550000003"})
        self.assertCode(c.post_json("/api/auth/otp-requests", {"phoneNumber": "+905550000003"}),
                        429, "rate_limit_exceeded")

        # --- sign in from the event link
        data = c.sign_in("+905550000002", event_code=code)
        self.assertEqual((data["nextStep"], data["isNewUser"], data["eventCode"]), ("consent", True, code))
        self.assertCode(c.get(f"{ev}/gallery"), 403, "consent_required")
        self.assertCode(c.get(f"{self.closed.code}".join(["/api/events/", "/gallery"])), 423, "event_access_closed")
        self.assertCode(c.get(f"/api/events/{self.expired.code}/gallery"), 410, "event_expired")
        self.assertCode(c.get("/api/events/zzzzz/gallery"), 404, "event_not_found")

        # --- profile
        self.assertCode(c.patch_json("/api/me", {"fullName": "Seko Konac", "email": "seko@example.com"}),
                        200, "profile_updated")
        self.assertCode(c.patch_json("/api/me", {"fullName": "x", "email": "owner@example.com"}),
                        409, "email_already_in_use")
        self.assertCode(c.patch_json("/api/me", {"fullName": "", "email": None}), 422, "validation_error")
        r = c.patch("/api/me", {"fullName": "x"}, content_type="application/json")   # no X-CSRFToken
        self.assertCode(r, 403, "csrf_failed")

        # --- consent
        self.assertCode(c.post_json("/api/me/consent", {"granted": True, "version": "998"}), 409, "consent_version_outdated")
        self.assertCode(c.post_json("/api/me/consent", {"granted": False, "version": settings.KVKK_VERSION}),
                        422, "explicit_consent_required")
        self.assertCode(c.post_json("/api/me/consent", {"granted": True, "version": settings.KVKK_VERSION}),
                        201, "consent_recorded")

        # --- gallery without a selfie yet
        r = c.get(f"{ev}/gallery")
        self.assertCode(r, 200, "gallery_retrieved")
        self.assertEqual((r.json()["data"]["matching"], r.json()["data"]["total"]), ("no_selfie", 0))

        # --- selfie
        upload = lambda data, slot="primary": c.post("/api/me/selfies", {"file": _file(data), "slot": slot},
                                                     **c.csrf_headers())
        self.assertCode(upload(b"not an image"), 422, "unreadable_image")
        self.assertCode(upload(_gif()), 422, "unsupported_file_type")
        self.assertCode(upload(jpeg(seed=9), slot="third"), 422, "invalid_slot")
        r = upload(jpeg(seed=10))
        self.assertCode(r, 201, "selfie_accepted")
        self.assertEqual(r.json()["data"]["status"], "processing")
        selfie_id = r.json()["data"]["id"]
        r = c.get("/api/me/selfies")
        self.assertEqual(r.json()["data"]["selfieState"], "selfie_processing")
        self.assertEqual(c.get(f"{ev}/gallery").json()["data"]["matching"], "selfie_processing")
        process(Image.objects.get(pk=selfie_id), [face_of(ME, 99)])
        r = c.get("/api/me/selfies")
        self.assertCode(r, 200, "selfies_retrieved")
        self.assertEqual(r.json()["data"]["selfies"][0]["status"], "ready")
        self.assertEqual(c.get("/api/me").json()["data"]["selfieState"], "ready")
        self.assertEqual(c.get(f"/api/me/selfies/{selfie_id}/thumb.jpg")["Content-Type"], "image/jpeg")
        self.assertCode(c.get("/api/me/selfies/999999/thumb.jpg"), 404, "selfie_not_found")

        # --- gallery: photos of me
        r = c.get(f"{ev}/gallery")
        d = r.json()["data"]
        self.assertEqual((d["matching"], d["canBrowseAll"], d["photosTotal"], d["photosPending"]), ("ready", False, 4, 1))
        self.assertEqual([p["id"] for p in d["photos"]], [self.p_me.seq_str, self.p_me2.seq_str])
        self.assertCode(c.get(f"{ev}/gallery", {"view": "all"}), 403, "browse_all_not_allowed")
        self.assertCode(c.get(f"{ev}/gallery", {"view": "everything"}), 422, "validation_error")
        r = c.get(f"{ev}/gallery", {"limit": 1})
        self.assertEqual((len(r.json()["data"]["photos"]), r.json()["data"]["nextCursor"]), (1, 1))

        # --- photo detail and files
        r = c.get(f"{ev}/gallery/photos/{self.p_me.seq_str}")
        self.assertCode(r, 200, "gallery_photo_retrieved")
        self.assertEqual((r.json()["data"]["nextPhotoId"], r.json()["data"]["downloadsLeftToday"]), (self.p_me2.seq_str, 3))
        self.assertCode(c.get(f"{ev}/gallery/photos/{self.p_other.seq_str}"), 404, "photo_not_found")
        base = f"{ev}/photos/{self.p_me.seq_str}"
        self.assertEqual(c.get(f"{base}/thumb.jpg")["Content-Type"], "image/jpeg")
        self.assertEqual(c.get(f"{base}/display.jpg")["Content-Type"], "image/jpeg")
        self.assertCode(c.get(f"{ev}/photos/{self.p_other.seq_str}/display.jpg"), 404, "photo_not_found")
        for _ in range(settings.DOWNLOAD_CAP_PER_PHOTO_PER_DAY):
            r = c.get(f"{base}/download")
            self.assertEqual(r.status_code, 200)
            self.assertIn("attachment", r["Content-Disposition"])
        self.assertCode(c.get(f"{base}/download"), 429, "download_limit_reached")
        self.assertEqual(c.get(f"{ev}/gallery/photos/{self.p_me.seq_str}").json()["data"]["downloadsLeftToday"], 0)

        # --- ZIP download: photos outside the view and photos at the cap are left out
        ids = ",".join(p.seq_str for p in (self.p_me, self.p_me2, self.p_other))
        r = c.get(f"{ev}/download.zip", {"photos": ids})
        self.assertEqual((r.status_code, r["Content-Type"]), (200, "application/zip"))
        self.assertEqual((r["X-Photos-Included"], r["X-Photos-Skipped"]), ("1", "2"))
        self.assertIn(f'filename="cleverphotos_{self.event.code}_mine.zip"', r["Content-Disposition"])
        with zipfile.ZipFile(io.BytesIO(b"".join(r.streaming_content))) as zf:
            self.assertEqual(zf.namelist(), [f"cleverphotos_{self.event.code}_{self.p_me2.seq:05d}.jpg"])
            self.assertEqual(zf.read(zf.namelist()[0])[:2], b"\xff\xd8")   # a JPEG
        r = c.get(f"{ev}/download.zip", {"all": "true"})
        self.assertEqual((r["X-Photos-Included"], r["X-Photos-Skipped"]), ("1", "1"))
        b"".join(r.streaming_content)
        self.assertCode(c.get(f"{ev}/download.zip"), 422, "validation_error")
        self.assertCode(c.get(f"{ev}/download.zip", {"photos": self.p_me.seq_str}), 429, "download_limit_reached")
        self.assertCode(c.get(f"{ev}/download.zip", {"photos": self.p_other.seq_str}), 404, "photo_not_found")
        with self.settings(ZIP_MAX_FILES=1):
            self.assertCode(c.get(f"{ev}/download.zip", {"all": "true"}), 422, "too_many_files")

        # --- my events
        r = c.get("/api/me/events")
        self.assertCode(r, 200, "user_events_retrieved")
        mine = {e["code"]: e for e in r.json()["data"]["events"]}
        self.assertEqual(mine[code]["matchedPhotoCount"], 2)

        # --- delete selfie, sign out
        self.assertEqual(c.delete_(f"/api/me/selfies/{selfie_id}").status_code, 204)
        self.assertCode(c.delete_(f"/api/me/selfies/{selfie_id}"), 404, "selfie_not_found")
        self.assertEqual(c.delete_("/api/auth/session").status_code, 204)
        self.assertCode(c.get("/api/me"), 401, "authentication_required")

        # --- every API endpoint was called (add new endpoints to this test)
        called = {(m, _norm(route)) for m, route, _ in c.checked}
        documented = {(m.upper(), _norm(path)) for path, ops in api.get_openapi_schema()["paths"].items() for m in ops
                      if not path.startswith(("/api/organizer/", "/api/shop/", "/api/me/billing"))}   # events/tests_organizer.py, shop/tests.py
        self.assertEqual(documented - called, set(), "endpoints never called by this test")


def _file(data: bytes):
    from django.core.files.uploadedfile import SimpleUploadedFile
    return SimpleUploadedFile("selfie.jpg", data, content_type="image/jpeg")


def _gif() -> bytes:
    import io

    from PIL import Image as PILImage
    buf = io.BytesIO()
    PILImage.new("RGB", (10, 10)).save(buf, "GIF")
    return buf.getvalue()

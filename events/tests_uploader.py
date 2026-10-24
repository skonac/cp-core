"""Uploaders: an organizer adds a phone; that phone signs in without an account and uploads to the
organizer's events on the organizer's credits, seeing only its own photos; revoking works at once.
Every /api/uploader/ and /api/organizer/uploaders endpoint is called (last check)."""
import json
import re

from django.core.cache import cache
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase

from accounts.models import OtpChallenge, UploaderAccess, User
from common.testing import ApiClient, TempMedia, add_photo, jpeg, make_event
from config.api import api
from events.models import Event

ORG, OTHER_ORG, PH = "+905550000201", "+905550000202", "+905550000203"


def _norm(route: str) -> str:
    return re.sub(r"<(?:\w+:)?\w+>|\{\w+\}", "{}", route.strip("/"))


def _file(seed):
    return SimpleUploadedFile("x.jpg", jpeg(seed=seed), content_type="image/jpeg")


class UploaderTests(TempMedia, TestCase):
    def setUp(self):
        cache.clear()
        self.org = User.objects.create_user(ORG, is_organizer=True, credit_balance=10, full_name="Futuruniq")
        self.other = User.objects.create_user(OTHER_ORG, is_organizer=True, credit_balance=10)
        self.event = make_event(self.org, name="Derya & Can")
        self.org_photo = add_photo(self.event, 1)                      # the organizer's own upload
        self.expired = make_event(self.org, status=Event.Status.EXPIRED)
        self.foreign = make_event(self.other)
        self.c = ApiClient()

    def assertCode(self, r, status, code):
        self.assertEqual((r.status_code, r.json().get("code")), (status, code), r.content[:400])

    def sign_in_again(self, phone, role="organizer"):
        """Sign out, then in (the OTP resend limit is not what this test is about)."""
        OtpChallenge.objects.all().delete()
        self.c.delete_("/api/auth/session")
        return self.c.sign_in(phone, role=role)

    def as_organizer(self, phone=ORG):
        self.sign_in_again(phone)

    def test_flow_and_contract(self):
        c = self.c
        # --- the organizer adds an uploader
        self.as_organizer()
        r = c.post_json("/api/organizer/uploaders", {"fullName": "Mert", "phoneNumber": "+90 555 000 02 03"})
        self.assertCode(r, 201, "organizer_uploader_created")
        ph = r.json()["data"]["uploader"]
        self.assertEqual((ph["phoneNumber"], ph["status"], ph["lastLoginAt"]), (PH, "active", None))
        self.assertCode(c.post_json("/api/organizer/uploaders", {"fullName": "Mert", "phoneNumber": PH}),
                        409, "uploader_already_exists")
        self.assertCode(c.post_json("/api/organizer/uploaders", {"fullName": "X", "phoneNumber": "123"}),
                        422, "invalid_phone_number")
        self.assertCode(c.get("/api/organizer/uploaders"), 200, "organizer_uploaders_retrieved")
        UploaderAccess.objects.create(organizer=self.other, phone=PH, full_name="Mert M.")   # a second organizer

        # --- the uploader signs in with the organizer entry: no account is created
        c.delete_("/api/auth/session")
        data = c.sign_in(PH, role="organizer")
        self.assertEqual((data["nextStep"], data["user"]["id"], data["user"]["accountRoles"]), ("uploader_home", "", []))
        self.assertEqual([a["organizerName"] for a in data["uploaderAccesses"]], ["Futuruniq", "Organizatör"])
        self.assertFalse(User.objects.filter(phone=PH).exists())
        r = c.get("/api/me")
        self.assertCode(r, 200, "profile_retrieved")
        self.assertEqual((r.json()["data"]["id"], r.json()["data"]["fullName"]), ("", "Mert"))
        self.assertCode(c.get("/api/organizer/dashboard"), 401, "authentication_required")
        self.assertCode(c.patch_json("/api/me", {"fullName": "x"}), 401, "authentication_required")
        self.assertIsNotNone(UploaderAccess.objects.get(pk=ph["id"]).last_login_at)

        # --- accesses, events (the organizer's, not expired, no join link)
        r = c.get("/api/uploader/accesses")
        self.assertCode(r, 200, "uploader_accesses_retrieved")
        access = r.json()["data"]["accesses"][0]["id"]
        self.assertEqual(access, ph["id"])
        root = f"/api/uploader/accesses/{access}/events"
        r = c.get(root)
        self.assertCode(r, 200, "uploader_events_retrieved")
        self.assertEqual([e["code"] for e in r.json()["data"]["events"]], [self.event.display_code])
        self.assertIsNone(r.json()["data"]["events"][0]["joinUrl"])
        ev = f"{root}/{self.event.code}"
        self.assertCode(c.get(ev), 200, "uploader_event_retrieved")
        self.assertEqual(c.get(f"{ev}/cover/thumb.jpg")["Content-Type"], "image/jpeg")
        self.assertCode(c.get(f"{root}/{self.foreign.code}"), 404, "event_not_found")
        self.assertCode(c.get(f"{root}/{self.expired.code}"), 404, "event_not_found")
        self.assertCode(c.get("/api/uploader/accesses/999999/events"), 404, "uploader_access_not_found")

        # --- upload on the organizer's credits
        r = c.post_json(f"{ev}/photo-upload-check", {"photoCount": 2})
        self.assertCode(r, 200, "photo_upload_eligibility_checked")
        self.assertEqual(r.json()["data"]["creditBalance"], 9)
        self.assertCode(c.post(f"{ev}/photos", {"files": [_file(2)]}), 403, "csrf_failed")
        r = c.post(f"{ev}/photos", {"files": [_file(2), _file(3)], "clientIds": ["a", "b"]}, **c.csrf_headers())
        self.assertCode(r, 200, "uploader_photos_uploaded")
        self.assertEqual((r.json()["data"]["acceptedCount"], r.json()["data"]["creditBalance"]), (2, 7))
        mine = [x["photoId"] for x in r.json()["data"]["results"]]
        self.assertEqual(set(self.event.images.filter(seq__in=[int(m) for m in mine]).values_list("owner_id", flat=True)),
                         {self.org.pk})

        # --- only its own photos: list, files, delete
        r = c.get(f"{ev}/photos")
        self.assertCode(r, 200, "uploader_photos_retrieved")
        self.assertEqual(([p["id"] for p in r.json()["data"]["photos"]], r.json()["data"]["counts"]["queued"]), (mine, 2))
        self.assertEqual(r.json()["data"]["photos"][0]["uploadedBy"], "Mert")
        self.assertEqual(c.get(f"{ev}/photos/{mine[0]}/thumb.jpg")["Content-Type"], "image/jpeg")
        self.assertEqual(c.get(f"{ev}/photos/{mine[0]}/display.jpg")["Content-Type"], "image/jpeg")
        self.assertCode(c.get(f"{ev}/photos/{self.org_photo.seq_str}/thumb.jpg"), 404, "photo_not_found")
        r = c.delete(f"{ev}/photos", json.dumps({"photoIds": [mine[1], self.org_photo.seq_str]}),
                     content_type="application/json", **c.csrf_headers())
        self.assertCode(r, 200, "uploader_photos_deleted")
        self.assertEqual(r.json()["data"]["affectedCount"], 1)
        self.org_photo.refresh_from_db()
        self.assertIsNone(self.org_photo.deleted_at)

        # --- the organizer sees who uploaded, and revokes
        self.as_organizer()
        photos = {p["id"]: p["uploadedBy"] for p in c.get(f"/api/organizer/events/{self.event.code}/photos").json()["data"]["photos"]}
        self.assertEqual((photos[mine[0]], photos[self.org_photo.seq_str]), ("Mert", None))
        r = c.delete_(f"/api/organizer/uploaders/{ph['id']}")
        self.assertCode(r, 200, "organizer_uploader_revoked")
        self.assertEqual(r.json()["data"]["uploader"]["status"], "revoked")
        self.assertCode(c.delete_("/api/organizer/uploaders/999999"), 404, "uploader_not_found")

        # --- revoked: that organizer is gone at once; the other one remains
        self.sign_in_again(PH)
        ids = [a["id"] for a in c.get("/api/uploader/accesses").json()["data"]["accesses"]]
        self.assertNotIn(ph["id"], ids)
        self.assertCode(c.get(root), 404, "uploader_access_not_found")
        c.delete_("/api/auth/session")
        self.assertCode(c.get("/api/uploader/accesses"), 401, "authentication_required")
        self.assertCode(c.get("/api/me"), 401, "authentication_required")

        # --- every uploader endpoint was called
        called = {(m, _norm(route)) for m, route, _ in c.checked}
        documented = {(m.upper(), _norm(path)) for path, ops in api.get_openapi_schema()["paths"].items() for m in ops
                      if path.startswith(("/api/uploader/", "/api/organizer/uploaders"))}
        self.assertEqual(documented - called, set(), "uploader endpoints never called by this test")

    def test_existing_account_signs_in_as_itself(self):
        UploaderAccess.objects.create(organizer=self.org, phone=PH, full_name="Mert")
        guest = User.objects.create_user(PH, is_participant=True, full_name="Mert Guest")
        data = self.c.sign_in(PH, role="organizer")
        self.assertEqual((data["nextStep"], data["user"]["id"]), ("uploader_home", str(guest.pk)))
        guest.refresh_from_db()
        self.assertFalse(guest.is_organizer)                         # no organizer role added
        self.assertCode(self.c.get("/api/uploader/accesses"), 200, "uploader_accesses_retrieved")

    def test_organizer_who_is_also_a_uploader(self):
        UploaderAccess.objects.create(organizer=self.other, phone=ORG, full_name="Seko")
        data = self.c.sign_in(ORG, role="organizer")
        self.assertEqual((data["nextStep"], len(data["uploaderAccesses"])), ("organizer_home", 1))
        self.assertCode(self.c.get("/api/uploader/accesses"), 200, "uploader_accesses_retrieved")

    def test_participant_sign_in_drops_uploader_rights(self):
        UploaderAccess.objects.create(organizer=self.org, phone=PH, full_name="Mert")
        self.c.sign_in(PH, role="organizer")
        self.c.sign_in("+905550000299")                              # someone else, same browser
        self.assertEqual(self.c.get("/api/uploader/accesses").json()["data"]["accesses"], [])

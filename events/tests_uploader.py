"""Uploaders: an organizer adds a phone for some of their events; that phone signs in without an account
and uploads to those events on the organizer's credits, seeing only its own photos; changing the
events and revoking work at once.
Every /api/uploader/ and /api/organizer/uploaders endpoint is called (last check)."""
import json
import re

from django.core.cache import cache
from django.core.files.uploadedfile import SimpleUploadedFile
from django.conf import settings
from django.test import TestCase, override_settings

from accounts.models import EmailChallenge, OtpChallenge, UploaderAccess, User
from media.models import Image
from notify.models import Notification
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
        self.event2 = make_event(self.org, name="Ece & Ali")         # not assigned at first
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
        self.assertCode(c.post_json("/api/organizer/uploaders", {"fullName": "Mert", "phoneNumber": PH,
                                                                 "eventCodes": [self.foreign.code]}), 422, "validation_error")
        self.assertCode(c.post_json("/api/organizer/uploaders", {"fullName": "Mert", "phoneNumber": PH,
                                                                 "eventCodes": [self.expired.code]}), 422, "validation_error")
        r = c.post_json("/api/organizer/uploaders", {"fullName": "Mert", "phoneNumber": "+90 555 000 02 03",
                                                     "eventCodes": [self.event.code.upper()]})
        self.assertCode(r, 201, "organizer_uploader_created")
        ph = r.json()["data"]["uploader"]
        self.assertEqual((ph["phoneNumber"], ph["status"], ph["lastLoginAt"]), (PH, "active", None))
        self.assertEqual([e["code"] for e in ph["events"]], [self.event.display_code])
        self.assertEqual(c.get("/api/organizer/uploaders", {"event": self.event2.code}).json()["data"]["total"], 0)
        self.assertEqual(c.get("/api/organizer/uploaders", {"event": self.event.code}).json()["data"]["total"], 1)
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
        self.assertCode(c.get(f"{root}/{self.event2.code}"), 404, "event_not_found")      # not assigned
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

        # --- the organizer sees who uploaded, changes the events
        self.as_organizer()
        photos = {p["id"]: p["uploadedBy"] for p in c.get(f"/api/organizer/events/{self.event.code}/photos").json()["data"]["photos"]}
        self.assertEqual((photos[mine[0]], photos[self.org_photo.seq_str]), ("Mert", None))
        up = f"/api/organizer/uploaders/{ph['id']}"
        r = c.patch_json(up, {"eventCodes": [self.event2.code], "fullName": "Mert Kaya"})
        self.assertCode(r, 200, "organizer_uploader_updated")
        self.assertEqual(([e["code"] for e in r.json()["data"]["uploader"]["events"]], r.json()["data"]["uploader"]["fullName"]),
                         ([self.event2.display_code], "Mert Kaya"))
        self.assertCode(c.patch_json(up, {"eventCodes": ["zzzzz"]}), 422, "validation_error")
        self.assertCode(c.patch_json("/api/organizer/uploaders/999999", {"eventCodes": []}), 404, "uploader_not_found")
        self.sign_in_again(PH)
        self.assertEqual([e["code"] for e in c.get(root).json()["data"]["events"]], [self.event2.display_code])
        self.assertCode(c.get(ev), 404, "event_not_found")                               # taken away at once
        self.assertCode(c.get(f"{ev}/photos"), 404, "event_not_found")

        # --- and revokes
        self.as_organizer()
        self.assertCode(c.post_json(f"/api/organizer/uploaders/{ph['id']}/invitation", {}), 422,
                        "validation_error")                           # a phone uploader has no email to mail
        r = c.delete_(f"/api/organizer/uploaders/{ph['id']}")
        self.assertCode(r, 200, "organizer_uploader_revoked")
        self.assertEqual(r.json()["data"]["uploader"]["status"], "revoked")
        self.assertCode(c.delete_("/api/organizer/uploaders/999999"), 404, "uploader_not_found")
        self.assertCode(c.patch_json(up, {"eventCodes": []}), 410, "uploader_revoked")

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
        self.assertEqual((data["nextStep"], len(data["uploaderAccesses"])), ("consent", 1))   # KVKK first
        self.assertCode(self.c.get("/api/uploader/accesses"), 200, "uploader_accesses_retrieved")

    def test_participant_sign_in_drops_uploader_rights(self):
        UploaderAccess.objects.create(organizer=self.org, phone=PH, full_name="Mert")
        self.c.sign_in(PH, role="organizer")
        self.c.sign_in("+905550000299")                              # someone else, same browser
        self.assertEqual(self.c.get("/api/uploader/accesses").json()["data"]["accesses"], [])


@override_settings(NOTIFY_SENDER="inline", EMAIL_DELIVERY="log")
class EmailUploaderTests(TempMedia, TestCase):
    """Uploaders by email (Nov 7): an invitation mail signs in to an account for that email; the account's
    verified email carries the upload rights; later sign-ins by email + password through the organizer entry."""

    def setUp(self):
        cache.clear()
        self.org = User.objects.create_user(ORG, is_organizer=True, credit_balance=10, full_name="Futuruniq",
                                            kvkk_version=settings.KVKK_VERSION)
        self.event = make_event(self.org, name="Derya & Can")
        self.c = ApiClient()
        self.c.force_login(self.org)
        self.c.get("/api/auth/csrf")

    def invitation(self):
        mail = Notification.objects.filter(channel="email").latest("id")
        return mail, EmailChallenge.objects.latest("created_at")

    def test_invite_sign_in_upload_and_password(self):
        c = self.c
        r = c.post_json("/api/organizer/uploaders", {"fullName": "Mert", "email": " Mert@Example.com",
                                                     "eventCodes": [self.event.display_code]})
        self.assertEqual(r.status_code, 201, r.content)
        up = r.json()["data"]["uploader"]
        self.assertEqual((up["email"], up["phoneNumber"]), ("mert@example.com", None))
        mail, ch = self.invitation()
        self.assertIn("Futuruniq", mail.title)
        self.assertNotIn(ch.dev_code, mail.text.split("/dj/auth/email/")[0])   # link only, no code to type
        self.assertIn("72 hours", mail.text) if "hours" in mail.text else self.assertIn("72 saat", mail.text)
        self.assertGreater((ch.expires_at - ch.created_at).total_seconds(), 71 * 3600)
        self.assertEqual((mail.to, ch.purpose, ch.role), ("mert@example.com", "invite", "organizer"))
        r = c.post_json("/api/organizer/uploaders", {"fullName": "Mert 2", "email": "mert@example.com"})
        self.assertEqual((r.status_code, r.json()["code"]), (409, "uploader_already_exists"))
        r = c.post_json("/api/organizer/uploaders", {"fullName": "Nobody"})
        self.assertEqual((r.status_code, r.json()["code"]), (422, "validation_error"))
        r = c.post_json("/api/organizer/uploaders", {"fullName": "X", "email": "not-an-email"})
        self.assertEqual((r.status_code, r.json()["code"]), (422, "invalid_email"))

        # the uploader opens the mail (another browser)
        u = ApiClient()
        u.get("/api/auth/csrf")
        d = u.post_json("/api/auth/email-verifications", {"challengeId": ch.pk, "code": ch.dev_code}).json()["data"]
        self.assertEqual((d["nextStep"], d["isNewUser"], [a["organizerName"] for a in d["uploaderAccesses"]]),
                         ("set_password", True, ["Futuruniq"]))
        acct = User.objects.get(email="mert@example.com")
        self.assertFalse(acct.is_organizer)                          # an uploader is no organizer
        r = u.put_json("/api/me/password", {"password": "foto12"})
        self.assertEqual(r.json()["data"]["nextStep"], "uploader_home")
        access_id = u.get("/api/uploader/accesses").json()["data"]["accesses"][0]["id"]
        root = f"/api/uploader/accesses/{access_id}/events"
        self.assertEqual([e["code"] for e in u.get(root).json()["data"]["events"]], [self.event.display_code])
        r = u.post(f"{root}/{self.event.display_code}/photos", {"files": [_file(5)]}, **u.csrf_headers())
        self.assertEqual((r.status_code, r.json()["data"]["acceptedCount"]), (200, 1), r.content)
        self.assertEqual(Image.objects.get(event=self.event, kind="photo", uploaded_by__email="mert@example.com").owner,
                         self.org)                                    # on the organizer's credits
        self.assertEqual(u.get(f"{root}/{self.event.display_code}/photos").json()["data"]["total"], 1)
        self.assertEqual(u.get("/api/organizer/events").status_code, 403)

        # later: email + password through the organizer entry
        u.delete_("/api/auth/session")
        u.get("/api/auth/csrf")
        r = u.post_json("/api/auth/password-sign-ins", {"identifier": "mert@example.com", "password": "foto12",
                                                       "role": "organizer"})
        self.assertEqual(r.json()["data"]["nextStep"], "uploader_home")
        acct.refresh_from_db()
        self.assertFalse(acct.is_organizer)
        self.assertEqual(u.get("/api/uploader/accesses").status_code, 200)
        self.assertIsNotNone(UploaderAccess.objects.get(email="mert@example.com").last_login_at)

        # resend, revoke: rights gone at once
        EmailChallenge.objects.all().delete()                         # (the mail limits are per 15 min, 30 s apart)
        r = c.post_json(f"/api/organizer/uploaders/{up['id']}/invitation", {})
        self.assertEqual((r.status_code, r.json()["code"]), (200, "organizer_uploader_invited"))
        r = c.post_json(f"/api/organizer/uploaders/{up['id']}/invitation", {})
        self.assertEqual((r.status_code, r.json()["code"]), (429, "rate_limit_exceeded"))   # 30 s apart
        c.delete_(f"/api/organizer/uploaders/{up['id']}")
        self.assertEqual(u.get(root).status_code, 404)
        self.assertEqual(c.post_json(f"/api/organizer/uploaders/{up['id']}/invitation", {}).status_code, 410)

    def test_existing_account_invited(self):
        """An email that already has an account: the invitation signs in to it; no new account."""
        old = User.objects.create_user(email="ece@example.com", password="secret1", is_participant=True)
        old.email_verified_at = old.created_at
        old.save()
        self.c.post_json("/api/organizer/uploaders", {"fullName": "Ece", "email": "ece@example.com",
                                                      "eventCodes": [self.event.display_code]})
        _, ch = self.invitation()
        u = ApiClient()
        u.get("/api/auth/csrf")
        d = u.post_json("/api/auth/email-verifications", {"token": "x" * 4, "challengeId": ch.pk,
                                                          "code": ch.dev_code}).json()
        self.assertEqual(d["code"], "invalid_email_code")            # a wrong token is wrong
        d = u.post_json("/api/auth/email-verifications", {"challengeId": ch.pk, "code": ch.dev_code}).json()["data"]
        self.assertEqual((d["user"]["id"], d["isNewUser"], d["nextStep"]), (str(old.pk), False, "uploader_home"))

    def test_unverified_email_gives_no_rights(self):
        """An account whose email was only typed in (never verified) gets no upload rights from it."""
        User.objects.create_user("+905550000299", "secret1", email="mert@example.com")
        UploaderAccess.objects.create(organizer=self.org, email="mert@example.com", full_name="Mert")
        u = ApiClient()
        u.get("/api/auth/csrf")
        u.post_json("/api/auth/password-sign-ins", {"identifier": "+905550000299", "password": "secret1"})
        self.assertEqual(u.get("/api/uploader/accesses").json()["data"]["accesses"], [])

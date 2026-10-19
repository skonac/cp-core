"""Organizer API: every /api/organizer/ endpoint called the way cp-web calls it, each response
checked against its declared schema (common.testing.ApiClient), plus the credit and access rules.
The last check fails when an organizer endpoint exists that this test never called."""
import json
import re

from django.core.cache import cache
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase

from accounts.models import User
from common.testing import ApiClient, TempMedia, add_selfie, face_of, jpeg, person, process
from config.api import api
from credits.models import CreditPackage, CreditTransaction
from events.models import Event, EventMember
from media.models import Image

ORG = "+905550000101"
GUEST = person(7)


def _norm(route: str) -> str:
    return re.sub(r"<(?:\w+:)?\w+>|\{\w+\}", "{}", route.strip("/"))


def _file(seed, name="x.jpg"):
    return SimpleUploadedFile(name, jpeg(seed=seed), content_type="image/jpeg")


class OrganizerApiTests(TempMedia, TestCase):
    def setUp(self):
        cache.clear()
        self.c = ApiClient()

    def assertCode(self, r, status, code):
        self.assertEqual((r.status_code, r.json().get("code")), (status, code), r.content[:400])

    def post_form(self, path, data):
        return self.c.post(path, data, **self.c.csrf_headers())

    def delete_json(self, path, body):
        return self.c.delete(path, json.dumps(body), content_type="application/json", **self.c.csrf_headers())

    def test_organizer_flow_and_contract(self):
        c = self.c
        # --- a participant is not an organizer
        c.sign_in("+905550000102")
        self.assertCode(c.get("/api/organizer/dashboard"), 403, "organizer_access_required")
        c.delete_("/api/auth/session")

        # --- organizer sign-in, empty dashboard
        c.sign_in(ORG, role="organizer")
        org = User.objects.get(phone=ORG)
        User.objects.filter(pk=org.pk).update(credit_balance=3, full_name="Seko")
        r = c.get("/api/organizer/dashboard")
        self.assertCode(r, 200, "organizer_dashboard_retrieved")
        self.assertEqual(r.json()["data"]["organizer"], {"displayName": "Seko", "creditBalance": 3})

        # --- create (multipart, with cover): open at once, cover free
        self.assertCode(self.post_form("/api/organizer/events", {"name": "", "date": "2026-06-12"}), 422, "validation_error")
        self.assertCode(self.post_form("/api/organizer/events", {"name": "X", "date": "2026-06-12",
                                                                 "hasFaceSearch": "false"}), 422, "invalid_event_settings")
        r = self.post_form("/api/organizer/events", {"name": "Derya & Can", "type": "graduation", "date": "2026-06-12",
                                                     "city": "İstanbul", "venue": "Çırağan", "cover": _file(1)})
        self.assertCode(r, 201, "organizer_event_created")
        ev = r.json()["data"]["event"]
        code = ev["code"]
        self.assertEqual((ev["status"], ev["type"], ev["coverIsUploaded"], ev["photoCount"]),
                         ("active", "graduation", True, 0))
        self.assertTrue(ev["joinUrl"].endswith(f"/?event={code}"))
        event = Event.objects.get(code=code.lower())
        self.assertEqual(event.cover_image.kind, Image.Kind.COVER)
        self.assertEqual(User.objects.get(pk=org.pk).credit_balance, 3)   # cover and event are free
        base = f"/api/organizer/events/{code}"
        self.assertEqual(c.get(f"{base}/cover/thumb.jpg")["Content-Type"], "image/jpeg")
        self.assertEqual(c.get(f"{base}/cover/display.jpg").status_code, 200)

        # --- read, list, edit
        self.assertCode(c.get(f"{base.lower()}"), 200, "organizer_event_retrieved")
        self.assertCode(c.get("/api/organizer/events/zzzzz"), 404, "event_not_found")
        r = c.get("/api/organizer/events")
        self.assertCode(r, 200, "organizer_events_retrieved")
        self.assertEqual(r.json()["data"]["total"], 1)
        r = c.patch_json(base, {"status": "access_closed", "allowsBrowseAll": True, "venue": "Bahçe"})
        self.assertCode(r, 200, "organizer_event_updated")
        self.assertEqual((r.json()["data"]["event"]["status"], r.json()["data"]["event"]["venue"]), ("access_closed", "Bahçe"))
        self.assertCode(c.patch_json(base, {"hasFaceSearch": False, "allowsBrowseAll": False}), 422, "invalid_event_settings")
        self.assertCode(c.patch_json(base, {"status": "expired"}), 422, "validation_error")
        self.assertCode(c.patch_json(base, {"status": "active"}), 200, "organizer_event_updated")

        # --- uploads: check, then a batch with a bad file and a duplicate; credits to the floor
        r = c.post_json(f"{base}/photo-upload-check", {"photoCount": 60})
        self.assertCode(r, 200, "photo_upload_eligibility_checked")
        self.assertEqual(r.json()["data"], {"eligible": False, "requiredCredits": 60, "creditBalance": 3,
                                            "remainingCredits": None, "missingCredits": 7})   # floor -50: 53 allowed
        r = c.post_json(f"{base}/photo-upload-check", {"photoCount": 5})
        self.assertEqual(r.json()["data"]["remainingCredits"], -2)
        bad = SimpleUploadedFile("bad.jpg", b"not an image", content_type="image/jpeg")
        r = self.post_form(f"{base}/photos", {"files": [_file(10), _file(11), bad, _file(10)],
                                              "clientIds": ["a", "b", "c", "d"]})
        self.assertCode(r, 200, "organizer_photos_uploaded")
        data = r.json()["data"]
        self.assertEqual((data["acceptedCount"], data["rejectedCount"], data["creditBalance"]), (2, 2, 1))
        self.assertEqual([(x["clientId"], x["status"], x["photoId"], x["errorCode"]) for x in data["results"]],
                         [("a", "queued", "00001", None), ("b", "queued", "00002", None),
                          ("c", "rejected", None, "unreadable_image"), ("d", "rejected", None, "duplicate_photo")])
        self.assertCode(self.post_form(f"{base}/photos", {"files": [_file(12)], "clientIds": ["x", "y"]}),
                        422, "validation_error")
        self.assertCode(self.post_form(f"{base}/photos", {"files": [_file(100 + i) for i in range(21)]}),
                        422, "too_many_files")
        User.objects.filter(pk=org.pk).update(credit_balance=-50)
        r = self.post_form(f"{base}/photos", {"files": [_file(13)]})
        self.assertEqual(r.json()["data"]["results"][0]["errorCode"], "insufficient_credits")
        User.objects.filter(pk=org.pk).update(credit_balance=100)
        self.post_form(f"{base}/photos", {"files": [_file(14)]})   # 00003

        # --- the worker processes two photos; a guest with a selfie matches photo 1
        p1, p2, p3 = Image.objects.filter(event=event, kind="photo").order_by("seq")
        process(p1, [face_of(GUEST, 1), face_of(person(8), 2)])
        process(p2, [face_of(person(9), 3)])
        guest = User.objects.create_user("+905550000103", full_name="Ece", kvkk_version="001")
        add_selfie(guest, GUEST)
        EventMember.objects.create(user=guest, event=event)
        EventMember.objects.create(user=User.objects.create_user("+905550000104"), event=event)

        # --- photo list, files, cover falls back to the first photo
        r = c.get(f"{base}/photos")
        self.assertCode(r, 200, "organizer_photos_retrieved")
        data = r.json()["data"]
        self.assertEqual(data["counts"], {"queued": 1, "processing": 0, "ready": 2, "failed": 0})
        self.assertEqual([(p["id"], p["status"], p["faceCount"], p["fileName"]) for p in data["photos"]],
                         [("00001", "ready", 2, "00001.jpg"), ("00002", "ready", 1, "00002.jpg"),
                          ("00003", "queued", None, "00003.jpg")])
        self.assertEqual(c.get(f"{base}/photos", {"status": "queued"}).json()["data"]["total"], 1)
        self.assertCode(c.get(f"{base}/photos", {"status": "bogus"}), 422, "validation_error")
        self.assertEqual(c.get(f"{base}/photos/00001/thumb.jpg")["Content-Type"], "image/jpeg")
        self.assertEqual(c.get(f"{base}/photos/00001/display.jpg").status_code, 200)
        self.assertCode(c.get(f"{base}/photos/00099/thumb.jpg"), 404, "photo_not_found")
        self.assertCode(c.delete_(f"{base}/cover"), 200, "organizer_cover_updated")
        r = c.get(base)
        self.assertEqual((r.json()["data"]["event"]["coverIsUploaded"], r.json()["data"]["event"]["coverThumbnailUrl"]),
                         (False, f"{base}/cover/thumb.jpg"))
        self.assertEqual(c.get(f"{base}/cover/thumb.jpg").status_code, 200)   # = photo 00001
        r = self.post_form(f"{base}/cover", {"file": _file(2)})
        self.assertCode(r, 200, "organizer_cover_updated")
        self.assertEqual(Image.objects.filter(event=event, kind="cover", deleted_at__isnull=True).count(), 1)

        # --- participants
        r = c.get(f"{base}/participants")
        self.assertCode(r, 200, "organizer_participants_retrieved")
        data = r.json()["data"]
        rows = {p["displayName"]: p for p in data["participants"]}
        self.assertEqual((rows["Ece"]["matchStatus"], rows["Ece"]["matchedPhotoCount"]), ("completed", 1))
        self.assertEqual((rows[None]["matchStatus"], rows[None]["matchedPhotoCount"]), ("consent_pending", None))
        self.assertEqual(data["counts"], {"completed": 1, "pending": 1, "failed": 0})
        self.assertNotIn("+905550000103", r.content.decode())

        # --- delete photos: no refund
        r = self.delete_json(f"{base}/photos", {"photoIds": ["00002", "00099", "abc"]})
        self.assertCode(r, 200, "organizer_photos_deleted")
        self.assertEqual(r.json()["data"]["affectedCount"], 1)
        self.assertEqual(c.get(base).json()["data"]["event"]["photoCount"], 2)

        # --- credit store and purchase
        CreditPackage.objects.create(name="1000cr", credits=1000, price=2000, sort_order=1)
        CreditPackage.objects.create(name="5000cr", credits=5000, price=7500, sort_order=2, is_featured=True)
        CreditPackage.objects.create(name="old", credits=10, price=1, is_active=False)
        CreditTransaction.objects.create(user=org, delta=1000, kind="purchase", ref="manual-1")
        r = c.get("/api/organizer/credits")
        self.assertCode(r, 200, "credit_store_retrieved")
        store = r.json()["data"]
        self.assertEqual([(p["name"], p["unitPrice"], p["discountPercent"]) for p in store["packages"]],
                         [("1000cr", 2.0, 0), ("5000cr", 1.5, 25)])
        self.assertEqual(len(store["purchases"]), 1)
        pid = store["packages"][0]["id"]
        self.assertCode(c.post_json("/api/organizer/credit-purchases", {"packageId": pid, "pricingVersion": "old"}),
                        409, "pricing_changed")
        self.assertCode(c.post_json("/api/organizer/credit-purchases", {"packageId": "999", "pricingVersion": "x"}),
                        404, "not_found")
        self.assertCode(c.post_json("/api/organizer/credit-purchases",
                                    {"packageId": pid, "pricingVersion": store["pricingVersion"]}),
                        501, "payment_not_available")

        # --- another organizer cannot see this event; expired events refuse changes
        c.delete_("/api/auth/session")
        c.sign_in("+905550000105", role="organizer")
        self.assertCode(c.get(base), 404, "event_not_found")
        c.delete_("/api/auth/session")
        c.force_login(org)   # a second OTP for the same phone within 30 s is refused
        c.get("/api/auth/csrf")
        Event.objects.filter(pk=event.pk).update(status=Event.Status.EXPIRED)
        self.assertCode(c.patch_json(base, {"name": "Y"}), 410, "event_expired")
        self.assertCode(self.post_form(f"{base}/photos", {"files": [_file(15)]}), 410, "event_expired")
        self.assertCode(c.post_json(f"{base}/photo-upload-check", {"photoCount": 1}), 410, "event_expired")
        self.assertCode(self.post_form(f"{base}/cover", {"file": _file(3)}), 410, "event_expired")
        self.assertEqual(c.get("/api/organizer/dashboard").json()["data"]["events"], [])
        self.assertEqual(c.get("/api/organizer/events", {"includeExpired": "true"}).json()["data"]["total"], 1)

        # --- signed out
        c.delete_("/api/auth/session")
        self.assertCode(c.get("/api/organizer/dashboard"), 401, "authentication_required")

        # --- every organizer endpoint was called
        called = {(m, _norm(route)) for m, route, _ in c.checked}
        documented = {(m.upper(), _norm(path)) for path, ops in api.get_openapi_schema()["paths"].items() for m in ops
                      if path.startswith("/api/organizer/")}
        self.assertEqual(documented - called, set(), "organizer endpoints never called by this test")

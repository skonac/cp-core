"""Guest access (accounts/guest.py): a QR visitor without an account, one event, deleted after a few hours."""
import json
from datetime import timedelta
from unittest import mock

from django.conf import settings
from django.core.cache import cache
from django.test import TestCase, override_settings
from django.utils import timezone

from common.testing import ApiClient, TempMedia, add_photo, face_of, jpeg, make_event, person, process
from events.models import Event, EventMember
from media.models import Image
from media.storage import abspath

from .models import ConsentLog, User

ME = person(1)


def _file(data: bytes):
    from django.core.files.uploadedfile import SimpleUploadedFile
    return SimpleUploadedFile("selfie.jpg", data, content_type="image/jpeg")


class GuestTests(TempMedia, TestCase):
    def setUp(self):
        cache.clear()
        self.org = User.objects.create_user("+905550000701", is_organizer=True, credit_balance=100)
        self.event = make_event(self.org)
        self.other = make_event(self.org, name="Other")
        self.c = ApiClient()
        self.c.get("/api/auth/csrf")

    def start(self, event=None, c=None):
        c = c or self.c
        return c.post_json("/api/auth/guest-sessions", {"eventCode": (event or self.event).display_code})

    def upload(self, seed, token="", c=None):
        c = c or self.c
        return c.post("/api/me/selfies", {"file": _file(jpeg(seed=seed)), "slot": "primary", "turnstileToken": token},
                      **c.csrf_headers())

    def consent(self):
        return self.c.post_json("/api/me/consent", {"granted": True, "version": settings.KVKK_VERSION})

    def test_full_guest_flow(self):
        r = self.c.get(f"/api/public/events/{self.event.display_code}")
        self.assertEqual(r.json()["data"]["allowsGuestAccess"], True)
        self.assertEqual(r.json()["data"]["turnstileSiteKey"], settings.TURNSTILE_TEST_SITE_KEY)

        r = self.start()
        self.assertEqual(r.status_code, 200, r.content)
        d = r.json()["data"]
        self.assertEqual((r.json()["code"], d["nextStep"], d["user"]["isGuest"], d["user"]["phoneNumber"],
                          d["user"]["email"]), ("guest_session_started", "consent", True, None, None))
        g = User.objects.get(pk=d["user"]["id"])
        self.assertEqual((g.is_guest, g.guest_event, g.has_usable_password()), (True, self.event, False))
        self.assertTrue(EventMember.objects.filter(user=g, event=self.event).exists())
        self.assertEqual(self.start().json()["data"]["user"]["id"], str(g.pk))   # same guest again

        self.assertEqual(self.consent().status_code, 201)
        log = ConsentLog.objects.get(user=g)
        self.assertEqual((log.phone, log.email), ("", ""))
        r = self.upload(10)
        self.assertEqual(r.status_code, 201, r.content)
        # matching works as for anyone
        selfie = Image.objects.get(owner=g, kind=Image.Kind.SELFIE)
        process(selfie, [face_of(ME, 1)])
        photo = add_photo(self.event, 20, [face_of(ME, 2)])
        r = self.c.get(f"/api/events/{self.event.display_code}/gallery")
        self.assertEqual((r.status_code, r.json()["data"]["total"]), (200, 1), r.content)
        r = self.c.get(f"/api/events/{self.event.display_code}/photos/{photo.seq_str}/download")
        self.assertEqual(r.status_code, 200)

        # not for guests: profile, password, organizer pages, other events
        for r in (self.c.patch_json("/api/me", {"fullName": "x"}), self.c.put_json("/api/me/password", {"password": "123456"})):
            self.assertEqual((r.status_code, r.json()["code"]), (403, "guest_not_allowed"))
        self.assertEqual(self.c.get("/api/organizer/events").status_code, 403)
        self.c.get(f"/api/public/events/{self.other.display_code}")   # looking doesn't join
        r = self.c.get(f"/api/events/{self.other.display_code}/gallery")
        self.assertEqual((r.status_code, r.json()["code"]), (404, "event_not_found"))
        self.assertFalse(EventMember.objects.filter(user=g, event=self.other).exists())
        r = self.c.get("/api/me/events")
        self.assertEqual([e["code"] for e in r.json()["data"]["events"]], [self.event.display_code])

        # the organizer sees a guest
        oc = ApiClient()
        oc.force_login(self.org)
        rows = oc.get(f"/api/organizer/events/{self.event.display_code}/participants").json()["data"]["participants"]
        self.assertEqual([(p["isGuest"], p["maskedPhone"]) for p in rows], [(True, None)])

    def test_selfie_rate_per_guest(self):
        self.start()
        self.consent()
        self.assertEqual(self.upload(1).status_code, 201)
        self.assertEqual(self.upload(2).status_code, 201)
        r = self.upload(3)
        self.assertEqual((r.status_code, r.json()["code"]), (429, "rate_limit_exceeded"))
        # another guest (another session) has its own limit; accounts have none
        c2 = ApiClient()
        c2.get("/api/auth/csrf")
        self.start(c=c2)
        c2.post_json("/api/me/consent", {"granted": True, "version": settings.KVKK_VERSION})
        self.assertEqual(self.upload(4, c=c2).status_code, 201)

    @override_settings(TURNSTILE_SECRET="real-secret", TURNSTILE_SITE_KEY="real-site")
    def test_turnstile(self):
        self.start()
        self.consent()
        r = self.upload(1)                       # no token
        self.assertEqual((r.status_code, r.json()["code"]), (403, "turnstile_failed"))

        def answer(success):
            resp = mock.MagicMock()
            resp.__enter__.return_value.read.return_value = json.dumps({"success": success}).encode()
            return resp
        with mock.patch("urllib.request.urlopen", return_value=answer(False)):
            self.assertEqual(self.upload(1, token="bad").status_code, 403)
        with mock.patch("urllib.request.urlopen", return_value=answer(True)) as urlopen:
            self.assertEqual(self.upload(1, token="good").status_code, 201)
        body = urlopen.call_args[0][0].data.decode()
        self.assertIn("secret=real-secret", body)
        self.assertIn("response=good", body)
        with mock.patch("urllib.request.urlopen", side_effect=TimeoutError()):
            self.assertEqual(self.upload(2, token="good").status_code, 403)   # Cloudflare down: refused

    def test_switches(self):
        self.start()
        self.event.is_guest_access = False
        self.event.save()
        r = self.c.get(f"/api/events/{self.event.display_code}/gallery")   # existing guest locked out at once
        self.assertEqual((r.status_code, r.json()["code"]), (403, "guest_access_disabled"))
        c2 = ApiClient()
        c2.get("/api/auth/csrf")
        r = self.start(c=c2)
        self.assertEqual((r.status_code, r.json()["code"]), (403, "guest_access_disabled"))
        self.assertFalse(self.c.get(f"/api/public/events/{self.event.display_code}").json()["data"]["allowsGuestAccess"])
        with self.settings(GUEST_ACCESS=False):
            r = self.start(self.other, c=c2)
            self.assertEqual((r.status_code, r.json()["code"]), (403, "guest_access_disabled"))
        self.other.status = Event.Status.CLOSED
        self.other.save()
        self.assertEqual(self.start(self.other, c=c2).status_code, 423)
        self.other.status = Event.Status.EXPIRED
        self.other.save()
        self.assertEqual(self.start(self.other, c=c2).status_code, 410)

    def test_expiry_and_purge(self):
        g = User.objects.get(pk=self.start().json()["data"]["user"]["id"])
        self.consent()
        self.upload(1)
        selfie = Image.objects.get(owner=g, kind=Image.Kind.SELFIE)
        path = abspath(selfie.relpath("d"))
        self.assertTrue(path.exists())
        User.objects.filter(pk=g.pk).update(created_at=timezone.now() - timedelta(hours=settings.GUEST_TTL_HOURS, minutes=1))
        self.assertEqual(self.c.get("/api/me").status_code, 401)   # signed out on the next request
        with self.captureOnCommitCallbacks(execute=True):
            self.c.get("/api/auth/csrf")
            self.start()                                            # a new guest: the purge runs
        self.assertFalse(User.objects.filter(pk=g.pk).exists())
        self.assertFalse(Image.objects.filter(pk=selfie.pk).exists())
        self.assertFalse(path.exists())
        self.assertTrue(ConsentLog.objects.filter(user=None).exists())   # the legal record stays

    def test_django_pages(self):
        r = self.c.get(f"/dj/g/{self.event.display_code}/")
        self.assertContains(r, "Continue without an account")
        r = self.c.post(f"/dj/g/{self.event.display_code}/", **self.c.csrf_headers())
        self.assertRedirects(r, f"/dj/e/{self.event.code}/", fetch_redirect_response=False)
        self.assertTrue(User.objects.get(is_guest=True))
        r = self.c.get(f"/dj/e/{self.other.code}/")
        self.assertEqual(r.status_code, 404)
        r = self.c.get("/dj/me/face/")
        self.assertEqual(r.status_code, 200)
        self.event.is_guest_access = False
        self.event.save()
        self.assertContains(self.c.get(f"/dj/g/{self.event.display_code}/"), "This event needs an account")

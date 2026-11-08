"""Email sign-up/sign-in (link + 4-digit code), passwords, password sign-in (accounts/emailauth.py)."""
import re
import time

from django.conf import settings
from django.core.cache import cache
from django.test import TestCase, override_settings

from common.testing import ApiClient
from events.models import Event, EventMember
from notify.models import Notification

from .emailauth import SESSION_PASSWORD_UNTIL
from .models import EmailChallenge, User


@override_settings(NOTIFY_SENDER="inline", EMAIL_DELIVERY="log")
class EmailSignInTests(TestCase):
    def setUp(self):
        cache.clear()
        self.c = ApiClient()
        self.c.get("/api/auth/csrf")

    def ask(self, email="ali@example.com", **body):
        r = self.c.post_json("/api/auth/email-requests", {"email": email, **body})
        self.assertEqual(r.status_code, 201, r.content)
        ch = EmailChallenge.objects.get(pk=r.json()["data"]["challengeId"])
        mail = Notification.objects.filter(channel="email").latest("id")
        token = re.search(r"/dj/auth/email/([\w-]+)/", mail.text).group(1)
        return ch, mail, token

    def verify(self, **body):
        return self.c.post_json("/api/auth/email-verifications", body)

    def test_sign_up_by_code_then_password(self):
        ch, mail, _ = self.ask(" Ali@Example.com", locale="en")
        self.assertEqual((ch.email, len(ch.dev_code)), ("ali@example.com", 4))
        self.assertEqual(mail.to, "ali@example.com")
        self.assertIn(ch.dev_code, mail.title)
        self.assertIn("http://testserver/dj/auth/email/", mail.text)
        self.assertIn("valid for 15 minutes", mail.text)
        r = self.verify(challengeId=ch.pk, code=ch.dev_code)
        self.assertEqual(r.status_code, 200, r.content)
        d = r.json()["data"]
        self.assertEqual((r.json()["code"], d["nextStep"], d["isNewUser"]), ("email_verified", "set_password", True))
        self.assertEqual((d["user"]["email"], d["user"]["phoneNumber"]), ("ali@example.com", None))
        u = User.objects.get(email="ali@example.com")
        self.assertTrue(u.is_participant and u.email_verified_at and not u.has_usable_password())

        self.assertEqual(self.c.put_json("/api/me/password", {"password": "12345"}).json()["code"], "password_too_short")
        r = self.c.put_json("/api/me/password", {"password": "123456"})   # any 6 characters
        self.assertEqual((r.status_code, r.json()["data"]["nextStep"]), (200, "consent"), r.content)   # KVKK first
        self.assertEqual(self.c.get("/api/me").status_code, 200)            # still signed in

        self.c.delete_("/api/auth/session")
        self.c.get("/api/auth/csrf")
        r = self.c.post_json("/api/auth/password-sign-ins", {"identifier": "ALI@example.com", "password": "123456"})
        self.assertEqual((r.status_code, r.json()["code"], r.json()["data"]["isNewUser"]), (200, "signed_in", False))
        # the code is used up
        self.assertEqual(self.verify(challengeId=ch.pk, code=ch.dev_code).json()["code"], "invalid_email_code")

    def test_link_once_and_django_pages(self):
        _, _, token = self.ask()
        page = self.c.get(f"/dj/auth/email/{token}/")       # mail scanners GET links: nothing happens
        self.assertContains(page, "Continue")
        self.assertEqual(EmailChallenge.objects.get().consumed_at, None)
        r = self.c.post(f"/dj/auth/email/{token}/", **self.c.csrf_headers())
        self.assertRedirects(r, "/set-password", fetch_redirect_response=False)   # on to cp-web
        # cp-web's password screen: no old password needed right after the link
        r = self.c.put_json("/api/me/password", {"password": "abcdef"})
        self.assertEqual((r.status_code, r.json()["data"]["nextStep"]), (200, "consent"))   # KVKK first
        self.assertTrue(User.objects.get(email="ali@example.com").check_password("abcdef"))
        self.assertEqual(self.verify(token=token).json()["code"], "invalid_email_code")   # once
        # an account that has a password: the link goes to cp-web's start page
        self.c.delete_("/api/auth/session")
        cache.clear()
        EmailChallenge.objects.all().delete()
        _, _, token = self.ask()
        r = self.c.post(f"/dj/auth/email/{token}/", **self.c.csrf_headers())
        self.assertRedirects(r, "/", fetch_redirect_response=False)
        # the Django test pages (code entry) still go to the Django password page
        r = self.c.post("/dj/auth/password/", {"password": "ghijkl"}, **self.c.csrf_headers())
        self.assertEqual(r.status_code, 302)

    def test_django_request_and_code_pages(self):
        r = self.c.post("/dj/auth/email/", {"email": "veli@example.com", "purpose": "sign_in", "role": "organizer",
                                            "locale": "tr"}, **self.c.csrf_headers())
        ch = EmailChallenge.objects.get()
        self.assertRedirects(r, f"/dj/auth/email/code/{ch.pk}/", fetch_redirect_response=False)
        r = self.c.post(f"/dj/auth/email/code/{ch.pk}/", {"code": "x"}, **self.c.csrf_headers())
        self.assertContains(r, "Kod hatalı")
        r = self.c.post(f"/dj/auth/email/code/{ch.pk}/", {"code": ch.dev_code}, **self.c.csrf_headers())
        self.assertRedirects(r, "/dj/auth/password/", fetch_redirect_response=False)
        self.assertTrue(User.objects.get(email="veli@example.com").is_organizer)

    def test_existing_account_sign_in_and_reset(self):
        u = User.objects.create_user(email="ali@example.com", password="oldpass", is_organizer=True,
                                     kvkk_version=settings.KVKK_VERSION)
        ch, _, _ = self.ask(role="organizer")
        d = self.verify(challengeId=ch.pk, code=ch.dev_code).json()["data"]
        self.assertEqual((d["nextStep"], d["isNewUser"], d["user"]["id"]), ("organizer_home", False, str(u.pk)))
        cache.clear()
        EmailChallenge.objects.all().delete()
        ch, mail, token = self.ask(purpose="reset")
        self.assertIn("şifre", mail.title)
        d = self.verify(token=token).json()["data"]
        self.assertEqual(d["nextStep"], "set_password")
        r = self.c.put_json("/api/me/password", {"password": "newpass"})   # no old password inside the window
        self.assertEqual(r.status_code, 200, r.content)
        # outside the window the current password is needed
        r = self.c.put_json("/api/me/password", {"password": "third1"})
        self.assertEqual((r.status_code, r.json()["code"]), (403, "current_password_invalid"))
        r = self.c.put_json("/api/me/password", {"password": "third1", "currentPassword": "newpass"})
        self.assertEqual(r.status_code, 200)
        u.refresh_from_db()
        self.assertTrue(u.check_password("third1"))

    def test_window_expires(self):
        User.objects.create_user(email="ali@example.com", password="oldpass")
        ch, _, _ = self.ask()
        self.verify(challengeId=ch.pk, code=ch.dev_code)
        s = self.c.session
        s[SESSION_PASSWORD_UNTIL] = time.time() - 1
        s.save()
        self.assertEqual(self.c.put_json("/api/me/password", {"password": "newpass"}).status_code, 403)

    def test_wrong_codes_lock_the_code_not_the_link(self):
        ch, _, token = self.ask()
        for _ in range(5):
            self.assertEqual(self.verify(challengeId=ch.pk, code="zzzz").status_code, 422)
        self.assertEqual(self.verify(challengeId=ch.pk, code=ch.dev_code).status_code, 429)
        self.assertEqual(self.verify(token=token).status_code, 200)

    def test_daily_wrong_code_cap(self):
        from datetime import timedelta
        from django.utils import timezone
        hour_ago, long_ago = timezone.now() - timedelta(hours=1), timezone.now() - timedelta(days=2)
        mk = lambda tag, attempts, at: EmailChallenge.objects.create(   # noqa: E731
            email="ali@example.com", code_hash="x", link_hash=tag, attempts=attempts, created_at=at,
            expires_at=timezone.now() + timedelta(hours=1))
        mk("a", 5, hour_ago)
        mk("b", 5, long_ago)                     # older than 24 h: not counted
        ch, _, _ = self.ask()
        self.assertEqual(self.verify(challengeId=ch.pk, code=ch.dev_code).status_code, 200)   # 5 today: fine
        mk("c", 5, hour_ago)                     # 10 today
        ch2 = mk("d", 0, hour_ago)
        self.assertEqual(self.verify(challengeId=ch2.pk, code="1234").status_code, 429)

    def test_request_limits_and_validation(self):
        self.ask()
        r = self.c.post_json("/api/auth/email-requests", {"email": "ali@example.com"})
        self.assertEqual((r.status_code, r.json()["code"]), (429, "rate_limit_exceeded"))   # 30 s apart
        for body, code in (({"email": "nope"}, "invalid_email"), ({"email": "a@b.co", "purpose": "x"}, "invalid_purpose"),
                           ({"email": "a@b.co", "role": "admin"}, "invalid_role")):
            r = self.c.post_json("/api/auth/email-requests", body)
            self.assertEqual((r.status_code, r.json()["code"]), (422, code))
        r = self.c.post_json("/api/auth/email-requests", {"email": "a@b.co", "eventCode": "ZZZZZ"})
        self.assertEqual(r.status_code, 404)

    def test_event_sign_up_and_membership(self):
        org = User.objects.create_user("+905550000601", is_organizer=True)
        event = Event.objects.create(owner=org, name="Düğün", type="wedding", date="2026-11-07")
        ch, _, _ = self.ask(eventCode=event.display_code)
        d = self.verify(challengeId=ch.pk, code=ch.dev_code).json()["data"]
        self.assertEqual((d["nextStep"], d["eventCode"]), ("set_password", event.display_code))
        u = User.objects.get(email="ali@example.com")
        self.assertTrue(EventMember.objects.filter(user=u, event=event).exists())
        d = self.c.put_json("/api/me/password", {"password": "123456"}).json()["data"]
        self.assertEqual(d["nextStep"], "consent")   # the event's next step

    def test_unverified_email_on_a_phone_account(self):
        phone_user = User.objects.create_user("+905550000602", email="ali@example.com")   # typed, never verified
        ch, _, _ = self.ask()
        d = self.verify(challengeId=ch.pk, code=ch.dev_code).json()["data"]
        self.assertTrue(d["isNewUser"])
        self.assertNotEqual(d["user"]["id"], str(phone_user.pk))
        phone_user.refresh_from_db()
        self.assertIsNone(phone_user.email)
        # a verified one is that account's
        cache.clear()
        EmailChallenge.objects.all().delete()
        other = User.objects.create_user("+905550000603", email="veli@example.com")
        other.email_verified_at = other.created_at
        other.save()
        ch, _, _ = self.ask("veli@example.com")
        self.assertEqual(self.verify(challengeId=ch.pk, code=ch.dev_code).json()["data"]["user"]["id"], str(other.pk))

    def test_password_sign_in_limits_and_role(self):
        u = User.objects.create_user("+905550000604", "secret1")
        r = self.c.post_json("/api/auth/password-sign-ins", {"identifier": "+905550000604", "password": "secret1",
                                                            "role": "organizer"})
        self.assertEqual((r.status_code, r.json()["data"]["nextStep"]), (200, "consent"))   # KVKK first
        u.refresh_from_db()
        u.kvkk_version = settings.KVKK_VERSION
        u.save()
        r = self.c.post_json("/api/auth/password-sign-ins", {"identifier": "+905550000604", "password": "secret1",
                                                            "role": "organizer"})
        self.assertEqual((r.status_code, r.json()["data"]["nextStep"]), (200, "organizer_home"))
        u.refresh_from_db()
        self.assertTrue(u.is_organizer)
        for _ in range(10):
            r = self.c.post_json("/api/auth/password-sign-ins", {"identifier": "+905550000604", "password": "nope"})
            self.assertEqual(r.json()["code"], "invalid_credentials")
        r = self.c.post_json("/api/auth/password-sign-ins", {"identifier": "+905550000604", "password": "secret1"})
        self.assertEqual((r.status_code, r.json()["code"]), (429, "rate_limit_exceeded"))

    def test_sign_up_of_a_registered_email(self):
        u = User.objects.create_user(email="ali@example.com", password="secret1")
        u.email_verified_at = u.created_at
        u.save()
        r = self.c.post_json("/api/auth/email-requests", {"email": "ALI@example.com", "purpose": "sign_up"})
        self.assertEqual((r.status_code, r.json()["code"]), (409, "email_already_registered"))
        self.assertFalse(Notification.objects.exists())                      # no mail
        self.ask(purpose="sign_in")                                           # an email-code sign-in still works
        cache.clear()
        EmailChallenge.objects.all().delete()
        # registered but no password yet (never finished): the sign-up mail goes out, it's their only way in
        User.objects.create_user(email="veli@example.com").save()
        ch, mail, _ = self.ask("veli@example.com", purpose="sign_up")
        self.assertIn("giriş kodun", mail.title)
        # a new email signs up as before
        cache.clear()
        ch, _, _ = self.ask("new@example.com", purpose="sign_up")
        self.assertEqual(self.verify(challengeId=ch.pk, code=ch.dev_code).json()["data"]["isNewUser"], True)
        # the Django page shows the links
        r = self.c.post("/dj/auth/email/", {"email": "ali@example.com", "purpose": "sign_up", "role": "participant"},
                        **self.c.csrf_headers())
        self.assertContains(r, "Forgot password?")

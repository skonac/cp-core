"""KVKK consent: versioned documents, consent record, consent log (design/core.md, "Consent")."""
from django.conf import settings
from django.core.cache import cache
from django.test import Client, TestCase, override_settings

from .models import ConsentLog, OtpChallenge, User


V = settings.KVKK_VERSION   # current document version (CP_KVKK_VERSION)


class KvkkConsentTests(TestCase):
    def setUp(self):
        cache.clear()   # rate limits
        self.c = Client(enforce_csrf_checks=True, HTTP_USER_AGENT="TestBrowser/1.0")

    def csrf(self):
        return {"HTTP_X_CSRFTOKEN": self.c.cookies["csrftoken"].value}

    def sign_in(self, phone="+905550000009"):
        self.c.get("/api/auth/csrf")
        r = self.c.post("/api/auth/otp-requests", {"phoneNumber": phone}, content_type="application/json", **self.csrf())
        self.assertEqual(r.status_code, 201, r.content)
        ch = OtpChallenge.objects.get(pk=r.json()["data"]["challengeId"])
        r = self.c.post("/api/auth/otp-verifications", {"challengeId": ch.pk, "code": ch.dev_code},
                        content_type="application/json", **self.csrf())
        self.assertEqual(r.status_code, 200, r.content)
        return User.objects.get(phone=phone)

    def consent(self, **body):
        return self.c.post("/api/me/consent", {"granted": True, **body}, content_type="application/json", **self.csrf())

    def me(self):
        return self.c.get("/api/me").json()["data"]

    def test_me_lists_current_documents(self):
        self.sign_in()
        d = self.me()
        self.assertFalse(d["hasConsent"])
        self.assertIsNone(d["consentVersion"])
        self.assertEqual(d["kvkk"], {"version": V, "urls": {"tr": f"/api/kvkk/kvkk_tr_{V}.html",
                                                             "en": f"/api/kvkk/kvkk_en_{V}.html"}})

    def test_consent_marks_user_and_logs(self):
        user = self.sign_in()
        r = self.consent(version=V, locale="en")
        self.assertEqual(r.status_code, 201, r.content)
        self.assertEqual(r.json()["data"], {"hasConsent": True, "version": V})
        user.refresh_from_db()
        self.assertEqual(user.kvkk_version, V)
        self.assertTrue(self.me()["hasConsent"])
        log = ConsentLog.objects.get()
        self.assertEqual((log.user, log.phone, log.version, log.locale), (user, user.phone, V, "en"))
        self.assertEqual(log.user_agent, "TestBrowser/1.0")
        self.assertEqual(log.ip, "127.0.0.1")
        self.assertEqual(len(log.sha256), 64)
        # Consenting again to the same version writes nothing new.
        self.assertEqual(self.consent(version=V).status_code, 201)
        self.assertEqual(ConsentLog.objects.count(), 1)

    def test_outdated_or_missing_version_rejected(self):
        self.sign_in()
        r = self.consent(version="998")
        self.assertEqual((r.status_code, r.json()["code"]), (409, "consent_version_outdated"))
        r = self.consent()
        self.assertEqual((r.status_code, r.json()["code"]), (422, "validation_error"))
        r = self.consent(version=V, locale="de")
        self.assertEqual(r.status_code, 422)
        r = self.c.post("/api/me/consent", {"granted": False, "version": V}, content_type="application/json", **self.csrf())
        self.assertEqual((r.status_code, r.json()["code"]), (422, "explicit_consent_required"))
        self.assertFalse(ConsentLog.objects.exists())

    def test_new_version_voids_consent(self):
        user = self.sign_in()
        self.consent(version=V)
        with override_settings(KVKK_VERSION="999"):
            user.refresh_from_db()
            self.assertFalse(user.is_consent)
            self.assertFalse(self.me()["hasConsent"])
            self.assertEqual(self.me()["consentVersion"], V)

    def test_log_survives_account_deletion(self):
        user = self.sign_in()
        self.consent(version=V)
        user.delete()
        log = ConsentLog.objects.get()
        self.assertIsNone(log.user)
        self.assertEqual(log.phone, "+905550000009")

    def test_documents_served(self):
        r = self.c.get(f"/api/kvkk/kvkk_tr_{V}.html")   # public
        self.assertEqual(r.status_code, 200)
        self.assertEqual((r["Content-Type"], r["X-Frame-Options"]), ("text/html; charset=utf-8", "SAMEORIGIN"))
        self.assertIn("Aydınlatma Metni".encode(), b"".join(r.streaming_content))
        self.assertEqual(self.c.head(f"/api/kvkk/kvkk_tr_{V}.html").status_code, 200)   # curl -I
        for bad in ("kvkk_tr_999.html", "kvkk_de_002.html", "kvkk_tr_002.pdf", "..%2Fsettings.py", "x.html"):
            self.assertEqual(self.c.get(f"/api/kvkk/{bad}").status_code, 404, bad)

    def test_django_consent_page(self):
        user = self.sign_in()
        r = self.c.get("/dj/consent/")
        self.assertContains(r, f"/api/kvkk/kvkk_en_{V}.html")
        token = r.cookies["csrftoken"].value if "csrftoken" in r.cookies else self.c.cookies["csrftoken"].value
        r = self.c.post("/dj/consent/", {"agree": "yes", "version": V, "locale": "en", "next": "/dj/me/",
                                      "csrfmiddlewaretoken": token})
        self.assertRedirects(r, "/dj/me/", fetch_redirect_response=False)
        user.refresh_from_db()
        self.assertTrue(user.is_consent)
        self.assertEqual(ConsentLog.objects.get().locale, "en")


class MasterPassTest(TestCase):
    """CP_MASTER_PASS (non-production): any OTP code, any account's password; off when empty."""

    def setUp(self):
        from accounts.models import User
        self.user = User.objects.create_user("+905550000077", "own-password")

    def _challenge(self):
        from django.core.cache import cache

        from accounts.otp import request_code
        cache.clear()   # rate limiter
        return request_code("+905550000077", "participant", None, "127.0.0.1")

    @override_settings(MASTER_PASS="12345")
    def test_otp_and_password(self):
        from django.contrib.auth import authenticate

        from accounts.otp import verify_code
        user, is_new, _, _ = verify_code(self._challenge().pk, "12345")
        self.assertEqual((user, is_new), (self.user, False))
        self.assertEqual(authenticate(username="+905550000077", password="12345"), self.user)
        self.assertEqual(authenticate(username="+905550000077", password="own-password"), self.user)
        self.assertIsNone(authenticate(username="+905550000077", password="1234"))
        self.assertTrue(self.client.login(phone="+905550000077", password="12345"))

    @override_settings(MASTER_PASS="")
    def test_off_when_empty(self):
        from django.contrib.auth import authenticate

        from accounts.otp import verify_code
        from common.api import ApiError
        with self.assertRaises(ApiError):
            verify_code(self._challenge().pk, "")
        self.assertIsNone(authenticate(username="+905550000077", password=""))

    @override_settings(MASTER_PASS="12345", IS_DEV=False)
    def test_check_refuses_production(self):
        from accounts.apps import check_master_pass
        self.assertEqual([e.id for e in check_master_pass()], ["cp.E003"])


class ContactApiTests(TestCase):
    """POST /api/contact/messages: validation, honeypot, rate limit, link to the account; contract-checked."""

    def setUp(self):
        from django.core.cache import cache
        from common.testing import ApiClient
        cache.clear()
        self.c = ApiClient()
        self.c.get("/api/auth/csrf")
        self.ok = {"name": "Ada", "email": "ada@example.com", "phone": None, "subject": "Kontör",
                   "message": "Merhaba", "locale": "tr", "privacyNoticeAccepted": True}

    def test_contact_flow_and_contract(self):
        import re
        from accounts.models import ContactMessage
        from config.api import api
        c = self.c
        r = c.post_json("/api/contact/messages", self.ok)
        self.assertEqual((r.status_code, r.json()["code"]), (201, "contact_message_received"))
        msg = ContactMessage.objects.get(pk=int(r.json()["data"]["messageId"]))
        self.assertEqual((msg.name, msg.phone, msg.user, msg.handled_at), ("Ada", "", None, None))

        r = c.post_json("/api/contact/messages", {**self.ok, "email": "nope", "message": " ", "privacyNoticeAccepted": False})
        self.assertEqual((r.status_code, r.json()["code"]), (422, "validation_error"))
        self.assertEqual(set(r.json()["errors"]), {"email", "message", "privacyNoticeAccepted"})
        self.assertEqual(c.post_json("/api/contact/messages", {"name": "x"}).status_code, 422)

        r = c.post_json("/api/contact/messages", {**self.ok, "website": "http://spam"})     # honeypot
        self.assertEqual((r.status_code, r.json()["data"]["messageId"]), (201, "0"))
        self.assertEqual(ContactMessage.objects.count(), 1)

        user = User.objects.create_user("+905550000501")
        c.force_login(user)
        c.get("/api/auth/csrf")
        c.post_json("/api/contact/messages", self.ok)
        self.assertEqual(ContactMessage.objects.latest("id").user, user)
        for _ in range(3):
            c.post_json("/api/contact/messages", self.ok)
        r = c.post_json("/api/contact/messages", self.ok)                                   # 6th from this IP
        self.assertEqual((r.status_code, r.json()["code"]), (429, "rate_limit_exceeded"))

        norm = lambda route: re.sub(r"<(?:\w+:)?\w+>|\{\w+\}", "{}", route.strip("/"))   # noqa: E731
        called = {(m, norm(route)) for m, route, _ in c.checked}
        documented = {(m.upper(), norm(path)) for path, ops in api.get_openapi_schema()["paths"].items() for m in ops
                      if path.startswith("/api/contact/")}
        self.assertEqual(documented - called, set())


class PhonelessAccountTests(TestCase):
    """Accounts without a phone (email sign-up, guests later): identity is the id; password sign-in
    takes a phone or an email (accounts.backends.IdentifierBackend)."""

    def test_accounts_without_phone_or_email(self):
        a, b = User.objects.create_user(), User.objects.create_user()   # NULLs don't collide
        self.assertIsNone(a.phone)
        self.assertEqual(str(b), f"#{b.pk}")
        c = User.objects.create_user(email=" Ali@Example.COM ")
        c.refresh_from_db()
        self.assertEqual(c.email, "ali@example.com")
        self.assertEqual(c.label, "ali@example.com")
        from django.db import IntegrityError, transaction
        with self.assertRaises(IntegrityError), transaction.atomic():
            User.objects.create_user(email="ALI@example.com")   # case-insensitive by storing lowercase
        u = User.objects.create_user("")   # "" from a form counts as no phone
        self.assertIsNone(u.phone)

    def test_password_sign_in_with_email_or_phone(self):
        from django.contrib.auth import authenticate
        e = User.objects.create_user(email="ali@example.com", password="secret1")
        p = User.objects.create_user("+905550000071", "secret2", email="veli@example.com")
        self.assertEqual(authenticate(username="ali@example.com", password="secret1"), e)
        self.assertEqual(authenticate(username=" ALI@example.com", password="secret1"), e)
        self.assertEqual(authenticate(username="+905550000071", password="secret2"), p)
        self.assertEqual(authenticate(username="veli@example.com", password="secret2"), p)
        self.assertIsNone(authenticate(username="ali@example.com", password="wrong"))
        self.assertIsNone(authenticate(username="nobody@example.com", password="secret1"))
        self.assertIsNone(authenticate(username="", password="secret1"))
        e.is_active = False
        e.save()
        self.assertIsNone(authenticate(username="ali@example.com", password="secret1"))

    def test_django_login_page_with_email(self):
        User.objects.create_user(email="ali@example.com", password="secret1", is_organizer=True)
        r = self.client.post("/dj/login/", {"username": "Ali@Example.com", "password": "secret1"})
        self.assertEqual(r.status_code, 302, r.content)
        self.assertTrue(self.client.get("/api/me").json()["data"]["id"])
        self.assertIsNone(self.client.get("/api/me").json()["data"]["phoneNumber"])

    @override_settings(MASTER_PASS="12345")
    def test_master_pass_with_email(self):
        from django.contrib.auth import authenticate
        u = User.objects.create_user(email="ali@example.com")
        self.assertEqual(authenticate(username="ali@example.com", password="12345"), u)

    def test_consent_log_copies_email(self):
        from . import kvkk
        u = User.objects.create_user(email="ali@example.com", full_name="Ali")
        self.assertTrue(kvkk.record_consent(u, "tr", "127.0.0.1", "UA"))
        log = ConsentLog.objects.get()
        self.assertEqual((log.phone, log.email, log.full_name), ("", "ali@example.com", "Ali"))

    def test_email_only_account_keeps_its_email(self):
        u = User.objects.create_user(email="ali@example.com", password="secret1")
        c = Client(enforce_csrf_checks=True)
        c.force_login(u)
        c.get("/api/auth/csrf")
        h = {"HTTP_X_CSRFTOKEN": c.cookies["csrftoken"].value}
        for email in (None, "other@example.com"):
            r = c.patch("/api/me", {"fullName": "Ali", "email": email}, content_type="application/json", **h)
            self.assertEqual((r.status_code, r.json()["code"]), (422, "email_is_sign_in"), r.content)
        r = c.patch("/api/me", {"fullName": "Ali", "email": "ALI@example.com"}, content_type="application/json", **h)
        self.assertEqual(r.status_code, 200, r.content)   # same address: only the name changes

    def test_staff_pages_and_masking_without_phone(self):
        from .otp import mask_phone
        self.assertIsNone(mask_phone(None))
        staff = User.objects.create_user("+905550000070", "pw", is_staff=True)
        u = User.objects.create_user(email="ali@example.com")
        self.client.force_login(staff)
        r = self.client.get("/dj/staff/users/")
        self.assertContains(r, "ali@example.com")
        self.assertContains(self.client.get(f"/dj/staff/users/{u.pk}/"), "ali@example.com")

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
        user, is_new, _ = verify_code(self._challenge().pk, "12345")
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

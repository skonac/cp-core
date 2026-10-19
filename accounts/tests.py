"""KVKK consent: versioned documents, consent record, consent log (design/core.md, "Consent")."""
from django.core.cache import cache
from django.test import Client, TestCase, override_settings

from .models import ConsentLog, OtpChallenge, User


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
        self.assertEqual(d["kvkk"], {"version": "000", "urls": {"tr": "/api/kvkk/kvkk_tr_000.pdf",
                                                                 "en": "/api/kvkk/kvkk_en_000.pdf"}})

    def test_consent_marks_user_and_logs(self):
        user = self.sign_in()
        r = self.consent(version="000", locale="en")
        self.assertEqual(r.status_code, 201, r.content)
        self.assertEqual(r.json()["data"], {"hasConsent": True, "version": "000"})
        user.refresh_from_db()
        self.assertEqual(user.kvkk_version, "000")
        self.assertTrue(self.me()["hasConsent"])
        log = ConsentLog.objects.get()
        self.assertEqual((log.user, log.phone, log.version, log.locale), (user, user.phone, "000", "en"))
        self.assertEqual(log.user_agent, "TestBrowser/1.0")
        self.assertEqual(log.ip, "127.0.0.1")
        self.assertEqual(len(log.sha256), 64)
        # Consenting again to the same version writes nothing new.
        self.assertEqual(self.consent(version="000").status_code, 201)
        self.assertEqual(ConsentLog.objects.count(), 1)

    def test_outdated_or_missing_version_rejected(self):
        self.sign_in()
        r = self.consent(version="999")
        self.assertEqual((r.status_code, r.json()["code"]), (409, "consent_version_outdated"))
        r = self.consent()
        self.assertEqual((r.status_code, r.json()["code"]), (422, "validation_error"))
        r = self.consent(version="000", locale="de")
        self.assertEqual(r.status_code, 422)
        r = self.c.post("/api/me/consent", {"granted": False, "version": "000"}, content_type="application/json", **self.csrf())
        self.assertEqual((r.status_code, r.json()["code"]), (422, "explicit_consent_required"))
        self.assertFalse(ConsentLog.objects.exists())

    def test_new_version_voids_consent(self):
        user = self.sign_in()
        self.consent(version="000")
        with override_settings(KVKK_VERSION="001"):
            user.refresh_from_db()
            self.assertFalse(user.is_consent)
            self.assertFalse(self.me()["hasConsent"])
            self.assertEqual(self.me()["consentVersion"], "000")

    def test_log_survives_account_deletion(self):
        user = self.sign_in()
        self.consent(version="000")
        user.delete()
        log = ConsentLog.objects.get()
        self.assertIsNone(log.user)
        self.assertEqual(log.phone, "+905550000009")

    def test_documents_served(self):
        r = self.c.get("/api/kvkk/kvkk_tr_000.pdf")   # public
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r["Content-Type"], "application/pdf")
        self.assertTrue(b"".join(r.streaming_content).startswith(b"%PDF"))
        for bad in ("kvkk_tr_999.pdf", "kvkk_de_000.pdf", "..%2Fsettings.py", "x.pdf"):
            self.assertEqual(self.c.get(f"/api/kvkk/{bad}").status_code, 404, bad)

    def test_django_consent_page(self):
        user = self.sign_in()
        r = self.c.get("/consent/")
        self.assertContains(r, "/api/kvkk/kvkk_en_000.pdf")
        token = r.cookies["csrftoken"].value if "csrftoken" in r.cookies else self.c.cookies["csrftoken"].value
        r = self.c.post("/consent/", {"agree": "yes", "version": "000", "locale": "en", "next": "/me/",
                                      "csrfmiddlewaretoken": token})
        self.assertRedirects(r, "/me/", fetch_redirect_response=False)
        user.refresh_from_db()
        self.assertTrue(user.is_consent)
        self.assertEqual(ConsentLog.objects.get().locale, "en")

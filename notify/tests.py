"""notify: OTP through the SMS router, the send log, the sender (inline and on its threads), failover,
retries, dropped devices, the restart pickup and the purge."""
import time
from datetime import timedelta
from unittest import mock

from django.core.cache import cache
from django.db import transaction
from django.test import TestCase, TransactionTestCase, override_settings
from django.utils import timezone

from accounts.models import OtpChallenge, User
from common.testing import ApiClient

from . import dispatch, providers, sender
from .models import Notification, PushDevice
from .providers import SendError, cpaas, iletimerkezi, zohomail


def failing(retry=False, gone=False):
    def send(*args):
        raise SendError("provider says no", retry=retry, gone=gone)
    return send


@override_settings(NOTIFY_SENDER="inline", SMS_DELIVERY="log")
class DispatchTests(TestCase):
    def setUp(self):
        cache.clear()
        self.user = User.objects.create_user("+905550000002")

    def device(self, endpoint="https://push.example/1"):
        return PushDevice.objects.create(user=self.user, kind=PushDevice.Kind.WEBPUSH, endpoint=endpoint)

    def test_otp_goes_through_the_router_and_keeps_no_row(self):
        c = ApiClient()
        c.get("/api/auth/csrf")
        with self.assertLogs("cp.notify", "WARNING") as logs:
            r = c.post_json("/api/auth/otp-requests", {"phoneNumber": "+905550000002"})
        self.assertEqual(r.status_code, 201)
        code = OtpChallenge.objects.get().dev_code
        self.assertIn(f"Cleverphotos kodun: {code}", logs.output[0])
        self.assertIn(f"@cleverphotos.org #{code}", logs.output[0])     # one-tap fill line
        self.assertEqual(Notification.objects.count(), 0)

    def test_otp_provider_failure_is_502_and_no_challenge(self):
        c = ApiClient()
        c.get("/api/auth/csrf")
        with mock.patch.dict(providers.SMS, {"log": failing(retry=True)}):
            r = c.post_json("/api/auth/otp-requests", {"phoneNumber": "+905550000002"})
        self.assertEqual((r.status_code, r.json()["code"]), (502, "sms_not_sent"))
        self.assertEqual(OtpChallenge.objects.count(), 0)

    def test_failover_to_the_next_provider(self):
        with mock.patch.dict(providers.SMS, {"a": failing(retry=True), "b": lambda phone, text: "B-1"}), \
                mock.patch.object(providers, "sms_route", lambda phone, purpose: ["a", "b"]):
            self.assertEqual(dispatch.otp("+905550000002", "123456"), "b")

    def test_no_route_is_an_error(self):
        with mock.patch.object(providers, "sms_route", lambda phone, purpose: []):
            with self.assertRaises(SendError):
                dispatch.otp("+15550000000", "123456")

    def test_sms_sent_after_commit_and_logged(self):
        with self.captureOnCommitCallbacks(execute=True):
            row = dispatch.sms("+905550000002", "Fotoğrafların hazır", kind="event_end", user=self.user)
            self.assertEqual(Notification.objects.get().status, "pending")   # not before the commit
        row.refresh_from_db()
        self.assertEqual((row.status, row.provider, row.attempts), ("sent", "log", 1))
        self.assertTrue(row.provider_ref.startswith("log-"))
        self.assertIsNotNone(row.sent_at)

    def test_rolled_back_sends_nothing(self):
        with self.captureOnCommitCallbacks(execute=True) as callbacks:
            try:
                with transaction.atomic():
                    dispatch.sms("+905550000002", "x", kind="test")
                    raise RuntimeError
            except RuntimeError:
                pass
        self.assertEqual((callbacks, Notification.objects.count()), ([], 0))

    def test_push_one_row_per_device_none_without(self):
        self.assertEqual(dispatch.push(self.user, "test", "T", "body"), [])
        self.device("https://push.example/1"), self.device("https://push.example/2")
        with self.captureOnCommitCallbacks(execute=True):
            rows = dispatch.push(self.user, "new_matches", "Derya & Can", "12 yeni fotoğrafın var", url="/e/RK238")
        self.assertEqual(len(rows), 2)
        self.assertEqual(set(Notification.objects.values_list("status", flat=True)), {"sent"})
        self.assertEqual(PushDevice.objects.filter(last_success_at__isnull=False).count(), 2)

    def test_gone_device_dropped(self):
        self.device()
        with mock.patch.dict(providers.PUSH, {"log": failing(gone=True)}), self.captureOnCommitCallbacks(execute=True):
            row, = dispatch.push(self.user, "test", "T", "body")
        row.refresh_from_db()
        self.assertEqual((row.status, PushDevice.objects.count()), ("failed", 0))

    def test_retry_until_max_attempts(self):
        with mock.patch.dict(providers.SMS, {"log": failing(retry=True)}):
            with self.captureOnCommitCallbacks(execute=True):
                row = dispatch.sms("+905550000002", "x", kind="test")
            row.refresh_from_db()
            self.assertEqual((row.status, row.attempts), ("pending", 1))      # left for a retry
            for _ in range(sender.MAX_ATTEMPTS - 1):
                sender.deliver(row.pk)
            row.refresh_from_db()
            self.assertEqual((row.status, row.attempts), ("failed", sender.MAX_ATTEMPTS))
        sender.deliver(row.pk)                                               # final: not claimed again
        row.refresh_from_db()
        self.assertEqual(row.attempts, sender.MAX_ATTEMPTS)

    def test_final_error_not_retried(self):
        with mock.patch.dict(providers.SMS, {"log": failing()}), self.captureOnCommitCallbacks(execute=True):
            row = dispatch.sms("+905550000002", "x", kind="test")
        row.refresh_from_db()
        self.assertEqual((row.status, row.error), ("failed", "provider says no"))

    def test_resume_picks_up_rows_a_dead_process_left(self):
        old = timezone.now() - timedelta(minutes=10)
        pending = Notification.objects.create(kind="test", channel="sms", to="+905550000002", text="a", created_at=old)
        stuck = Notification.objects.create(kind="test", channel="sms", to="+905550000002", text="b", created_at=old,
                                            status="sending", claimed_at=old, attempts=1)
        fresh = Notification.objects.create(kind="test", channel="sms", to="+905550000002", text="c",
                                            status="sending", claimed_at=timezone.now(), attempts=1)
        self.assertEqual(set(sender.resume_ids()), {pending.pk, stuck.pk})
        sender.queue(sender.resume_ids() + [fresh.pk])                      # fresh: another thread's live claim
        for row in (pending, stuck, fresh):
            row.refresh_from_db()
        self.assertEqual((pending.status, stuck.status, fresh.status), ("sent", "sent", "sending"))

    def test_purge(self):
        Notification.objects.create(kind="test", channel="sms", to="x", text="old", status="sent",
                                    created_at=timezone.now() - timedelta(days=15))
        sender._last_purge = None
        with self.captureOnCommitCallbacks(execute=True):
            dispatch.sms("+905550000002", "new", kind="test")
        self.assertEqual(list(Notification.objects.values_list("text", flat=True)), ["new"])


@override_settings(NOTIFY_SENDER="thread", SMS_DELIVERY="log")
class ThreadSenderTests(TransactionTestCase):
    """The real pool: the request returns before the (slow) provider call finishes."""

    def test_slow_provider_does_not_block_the_caller(self):
        def slow(phone, text):
            time.sleep(0.5)
            return "slow-1"
        with mock.patch.dict(providers.SMS, {"log": slow}):
            started = time.monotonic()
            row = dispatch.sms("+905550000002", "x", kind="test")     # autocommit: queued at once
            self.assertLess(time.monotonic() - started, 0.2)
            deadline = time.monotonic() + 5
            while time.monotonic() < deadline:
                row.refresh_from_db()
                if row.status == "sent":
                    break
                time.sleep(0.05)
        self.assertEqual((row.status, row.provider_ref), ("sent", "slow-1"))


class FakeHTTP:
    """Stands in for urllib.request.urlopen: records the request, answers like İletiMerkezi."""

    def __init__(self, code=200, body=None, error=None):
        self.code, self.error, self.requests = code, error, []
        self.body = body or {"response": {"status": {"code": str(code), "message": "x"}, "order": {"id": "777"}}}

    def __call__(self, req, timeout=None):
        import io
        import json
        import urllib.error
        self.requests.append((req.full_url, json.loads(req.data)))
        if self.error:
            raise self.error
        raw = json.dumps(self.body).encode()
        if self.code != 200:
            raise urllib.error.HTTPError(req.full_url, self.code, "x", {}, io.BytesIO(raw))
        response = io.BytesIO(raw)
        response.status = 200
        return _Ctx(response)


class _Ctx:
    def __init__(self, r):
        self.r = r

    def __enter__(self):
        return self.r

    def __exit__(self, *a):
        return False


@override_settings(NOTIFY_SENDER="inline", SMS_DELIVERY="live", ILETIMERKEZI_API_KEY="k", ILETIMERKEZI_API_HASH="h",
                   ILETIMERKEZI_SENDER="CLEVERPHOTO")
class IletimerkeziTests(TestCase):
    def test_send_body_and_order_id(self):
        fake = FakeHTTP()
        with mock.patch("urllib.request.urlopen", fake):
            self.assertEqual(dispatch.otp("+905551234567", "123456"), "iletimerkezi")
        url, body = fake.requests[0]
        self.assertEqual(url, "https://api.iletimerkezi.com/v1/send-sms/json")
        order = body["request"]["order"]
        self.assertEqual(body["request"]["authentication"], {"key": "k", "hash": "h"})
        self.assertEqual((order["sender"], order["iys"], order["message"]["receipents"]["number"]),
                         ("CLEVERPHOTO", "0", ["5551234567"]))
        self.assertTrue(order["message"]["text"].startswith("Cleverphotos kodun: 123456"))

    def test_notification_row_gets_order_id_and_delivery_report(self):
        with mock.patch("urllib.request.urlopen", FakeHTTP()), self.captureOnCommitCallbacks(execute=True):
            row = dispatch.sms("+905551234567", "Fotoğrafların hazır", kind="event_end")
        row.refresh_from_db()
        self.assertEqual((row.status, row.provider, row.provider_ref), ("sent", "iletimerkezi", "777"))
        report = {"report": {"id": 1, "packet_id": 777, "status": "delivered", "to": "+905551234567", "body": "x"}}
        r = self.client.post("/api/notify/iletimerkezi/report", report, content_type="application/json")
        self.assertEqual(r.status_code, 200)
        row.refresh_from_db()
        self.assertEqual(row.delivery, "delivered")
        self.assertEqual(self.client.post("/api/notify/iletimerkezi/report", "nope",
                                          content_type="application/json").status_code, 400)

    def test_errors_final_or_retryable(self):
        cases = [(FakeHTTP(code=402, body={"response": {"status": {"code": "402", "message": "Bakiye yetersiz"}}}), False),
                 (FakeHTTP(code=503, body={"response": {"status": {"code": "503", "message": "down"}}}), True),
                 (FakeHTTP(error=TimeoutError("timed out")), True)]
        for fake, retry in cases:
            with self.subTest(retry=retry), mock.patch("urllib.request.urlopen", fake):
                with self.assertRaises(SendError) as e:
                    iletimerkezi.sms("+905551234567", "x")
                self.assertEqual(e.exception.retry, retry)

    def test_non_turkish_number_has_no_route_yet(self):
        with self.assertRaises(SendError):
            dispatch.otp("+15551234567", "123456")

    def test_check_needs_credentials(self):
        from notify.apps import check_delivery
        with self.settings(ILETIMERKEZI_API_HASH=""):
            self.assertEqual([e.id for e in check_delivery()], ["cp.E011"])
        with self.settings(ILETIMERKEZI_SENDER="cleverphotos.org"):
            self.assertEqual([e.id for e in check_delivery()], ["cp.E012"])


class FakeZoho:
    """Stands in for urllib.request.urlopen: answers the token URL and the Mail API from queued (status, body)
    pairs per URL kind ("token", "api"); records (url, headers, body)."""

    def __init__(self, token=None, api=None):
        self.answers = {"token": list(token or [(200, {"access_token": "AT1", "expires_in": 3600})]),
                        "api": list(api or [(200, {"status": {"code": 200}, "data": {"messageId": "M1"}})])}
        self.requests = []

    def __call__(self, req, timeout=None):
        import io
        import json
        import urllib.error
        kind = "token" if req.full_url.startswith(zohomail.TOKEN_URL) else "api"
        body = req.data.decode() if req.data else ""
        self.requests.append((kind, req.full_url, dict(req.header_items()), body))
        item = self.answers[kind].pop(0) if len(self.answers[kind]) > 1 else self.answers[kind][0]
        if isinstance(item, Exception):
            raise item
        status, answer = item
        raw = json.dumps(answer).encode()
        if status != 200:
            raise urllib.error.HTTPError(req.full_url, status, "x", {}, io.BytesIO(raw))
        response = io.BytesIO(raw)
        response.status = 200
        return _Ctx(response)


@override_settings(NOTIFY_SENDER="inline", EMAIL_DELIVERY="live", ZOHOMAIL_CLIENT_ID="cid", ZOHOMAIL_CLIENT_SECRET="sec",
                   ZOHOMAIL_REFRESH_TOKEN="RT", ZOHOMAIL_ACCOUNT_ID="738", ZOHOMAIL_FROM="Clever Photos <contact@x.org>",
                   CPAAS_API_KEY="KEY", CPAAS_FROM="Clever Photos <noreply@x.org>", EMAIL_REPLY_TO="contact@x.org")
class ZohomailTests(TestCase):
    def setUp(self):
        zohomail._token.update(value="", expires=0.0)

    def test_send_through_dispatch_and_token_reused(self):
        import json
        fake = FakeZoho()
        with mock.patch("urllib.request.urlopen", fake), \
                mock.patch.object(providers, "email_route", lambda address, purpose: ["zohomail"]), \
                self.captureOnCommitCallbacks(execute=True):   # sends on leaving: keep it innermost
            row = dispatch.email("a@b.com", "Konu", "<p>Merhaba</p>", kind="test")
            dispatch.email("a@b.com", "Düz", "plain text", kind="test")
        row.refresh_from_db()
        self.assertEqual((row.status, row.provider, row.provider_ref), ("sent", "zohomail", "M1"))
        kinds = [r[0] for r in fake.requests]
        self.assertEqual(kinds, ["token", "api", "api"])          # one token for both mails
        _, url, headers, body = fake.requests[0]
        self.assertIn("grant_type=refresh_token", body)
        self.assertIn("refresh_token=RT", body)
        _, url, headers, body = fake.requests[1]
        self.assertEqual(url, "https://mail.zoho.eu/api/accounts/738/messages")
        self.assertEqual(headers["Authorization"], "Zoho-oauthtoken AT1")
        sent = json.loads(body)
        self.assertEqual((sent["fromAddress"], sent["toAddress"], sent["subject"], sent["mailFormat"]),
                         ("Clever Photos <contact@x.org>", "a@b.com", "Konu", "html"))
        self.assertEqual(json.loads(fake.requests[2][3])["mailFormat"], "plaintext")

    def test_token_renewed_before_expiry(self):
        fake = FakeZoho(token=[(200, {"access_token": "AT1", "expires_in": 200}),   # inside the early margin
                               (200, {"access_token": "AT2", "expires_in": 3600})])
        with mock.patch("urllib.request.urlopen", fake):
            zohomail.email("a@b.com", "s", "t")
            zohomail.email("a@b.com", "s", "t")
        self.assertEqual([r[0] for r in fake.requests], ["token", "api", "token", "api"])
        self.assertEqual(fake.requests[3][2]["Authorization"], "Zoho-oauthtoken AT2")

    def test_401_renews_the_token_once(self):
        fake = FakeZoho(token=[(200, {"access_token": "AT1", "expires_in": 3600}),
                               (200, {"access_token": "AT2", "expires_in": 3600})],
                        api=[(401, {"data": {"errorCode": "INVALID_OAUTHTOKEN"}, "status": {"code": 401}}),
                             (200, {"status": {"code": 200}, "data": {"messageId": "M2"}})])
        with mock.patch("urllib.request.urlopen", fake):
            self.assertEqual(zohomail.email("a@b.com", "s", "t"), "M2")
        self.assertEqual([r[0] for r in fake.requests], ["token", "api", "token", "api"])

    def test_errors_final_or_retryable(self):
        cases = [
            ("rejected refresh token", FakeZoho(token=[(200, {"error": "invalid_code"})]), False),
            ("token rate limit", FakeZoho(token=[(400, {"error": "Access Denied"})]), True),
            ("token server down", FakeZoho(token=[(503, {})]), True),
            ("unreachable", FakeZoho(token=[TimeoutError("timed out")]), True),
            ("bad from address", FakeZoho(api=[(400, {"status": {"code": 400, "description": "Invalid Input"},
                                                     "data": {"moreInfo": "Invalid fromAddress"}})]), False),
            ("api down", FakeZoho(api=[(500, {})]), True),
        ]
        for name, fake, retry in cases:
            zohomail._token.update(value="", expires=0.0)
            with self.subTest(name), mock.patch("urllib.request.urlopen", fake):
                with self.assertRaises(SendError) as e:
                    zohomail.email("a@b.com", "s", "t")
                self.assertEqual(e.exception.retry, retry)

    def test_log_mode_sends_nothing(self):
        with self.settings(EMAIL_DELIVERY="log"), mock.patch("urllib.request.urlopen") as urlopen, \
                self.captureOnCommitCallbacks(execute=True):
            row = dispatch.email("a@b.com", "s", "t", kind="test")
        row.refresh_from_db()
        self.assertEqual((row.status, row.provider), ("sent", "log"))
        urlopen.assert_not_called()

    def test_email_test_command(self):
        from io import StringIO

        from django.core.management import call_command
        fake = FakeZoho(api=[(200, {"status": {"code": 200}, "data": [
            {"accountId": "738", "primaryEmailAddress": "admin@x.org",
             "sendMailDetails": [{"fromAddress": "admin@x.org"}, {"fromAddress": "contact@x.org"}]}]}),
            (200, {"status": {"code": 200}, "data": {"messageId": "M9"}})])
        out = StringIO()
        with mock.patch("urllib.request.urlopen", fake):
            call_command("email_test", "a@b.com", "--accounts", "--via", "zohomail", stdout=out)
        self.assertIn("738  admin@x.org  from: admin@x.org, contact@x.org", out.getvalue())
        self.assertIn("sent via zohomail: M9", out.getvalue())

    def test_check_needs_settings(self):
        from notify.apps import check_email
        self.assertEqual(check_email(), [])
        with self.settings(ZOHOMAIL_ACCOUNT_ID=""):        # fallback on but incomplete
            self.assertEqual([e.id for e in check_email()], ["cp.E014"])
        with self.settings(ZOHOMAIL_REFRESH_TOKEN="", ZOHOMAIL_ACCOUNT_ID=""):   # fallback off: fine
            self.assertEqual(check_email(), [])
        with self.settings(CPAAS_API_KEY=""):
            self.assertIn("CP_CPAAS_API_KEY", check_email()[0].msg)
        with self.settings(EMAIL_DELIVERY="smtp"):
            self.assertEqual([e.id for e in check_email()], ["cp.E013"])


class FakeCpaas:
    """Stands in for urllib.request.urlopen for CPaaS (and, after a failure, Zoho Mail via FakeZoho)."""

    def __init__(self, status=201, answer=None, error=None, then=None):
        self.status, self.error, self.then, self.requests = status, error, then, []
        self.answer = answer or {"data": [{"code": "EM_104", "message": "Email request received"}],
                                 "message": "OK", "request_id": "R1", "object": "email"}

    def __call__(self, req, timeout=None):
        import io
        import json
        import urllib.error
        if not req.full_url.startswith(cpaas.URL):
            return self.then(req, timeout)
        self.requests.append((dict(req.header_items()), json.loads(req.data)))
        if self.error:
            raise self.error
        raw = json.dumps(self.answer).encode()
        if self.status >= 300:
            raise urllib.error.HTTPError(req.full_url, self.status, "x", {}, io.BytesIO(raw))
        response = io.BytesIO(raw)
        response.status = self.status
        return _Ctx(response)


@override_settings(NOTIFY_SENDER="inline", EMAIL_DELIVERY="live", CPAAS_API_KEY="KEY",
                   CPAAS_FROM="Clever Photos <noreply@x.org>", EMAIL_REPLY_TO="contact@x.org",
                   ZOHOMAIL_CLIENT_ID="cid", ZOHOMAIL_CLIENT_SECRET="sec", ZOHOMAIL_REFRESH_TOKEN="RT",
                   ZOHOMAIL_ACCOUNT_ID="738", ZOHOMAIL_FROM="Clever Photos <contact@x.org>")
class CpaasTests(TestCase):
    def setUp(self):
        zohomail._token.update(value="", expires=0.0)

    def test_send_body_and_request_id(self):
        fake = FakeCpaas()
        with mock.patch("urllib.request.urlopen", fake), self.captureOnCommitCallbacks(execute=True):
            row = dispatch.email("Ayşe <a@b.com>", "Konu", "<p>Merhaba</p>", kind="test")
            dispatch.email("a@b.com", "Düz", "plain text", kind="test")
        row.refresh_from_db()
        self.assertEqual((row.status, row.provider, row.provider_ref), ("sent", "cpaas", "R1"))
        headers, body = fake.requests[0]
        self.assertEqual(headers["Authorization"], "Zoho-enczapikey KEY")
        self.assertEqual(body, {"from": {"address": "noreply@x.org", "name": "Clever Photos"},
                                "to": [{"email_address": {"address": "a@b.com", "name": "Ayşe"}}],
                                "subject": "Konu", "htmlbody": "<p>Merhaba</p>",
                                "reply_to": [{"address": "contact@x.org"}]})
        self.assertEqual(fake.requests[1][1]["textbody"], "plain text")
        self.assertNotIn("htmlbody", fake.requests[1][1])

    def test_no_reply_to_when_unset(self):
        fake = FakeCpaas()
        with self.settings(EMAIL_REPLY_TO=""), mock.patch("urllib.request.urlopen", fake):
            cpaas.email("a@b.com", "s", "t")
        self.assertNotIn("reply_to", fake.requests[0][1])

    def test_errors_final_or_retryable(self):
        bad = {"error": {"code": "TM_3301", "message": "Invalid API Token found",
                         "details": [{"code": "SERR_157", "message": "Invalid API Token found"}]}}
        for name, fake, retry in [("bad key", FakeCpaas(status=401, answer=bad), False),
                                  ("server down", FakeCpaas(status=503, answer={}), True),
                                  ("unreachable", FakeCpaas(error=TimeoutError("timed out")), True)]:
            with self.subTest(name), mock.patch("urllib.request.urlopen", fake):
                with self.assertRaises(SendError) as e:
                    cpaas.email("a@b.com", "s", "t")
                self.assertEqual(e.exception.retry, retry)
        with mock.patch("urllib.request.urlopen", FakeCpaas(status=401, answer=bad)):
            with self.assertRaisesRegex(SendError, "TM_3301 - Invalid API Token found"):
                cpaas.email("a@b.com", "s", "t")

    def test_falls_back_to_zoho_mail(self):
        fake = FakeCpaas(status=503, answer={}, then=FakeZoho())
        with mock.patch("urllib.request.urlopen", fake), self.captureOnCommitCallbacks(execute=True):
            row = dispatch.email("a@b.com", "s", "t", kind="test")
        row.refresh_from_db()
        self.assertEqual((row.status, row.provider, row.provider_ref), ("sent", "zohomail", "M1"))

    def test_route_without_the_fallback(self):
        self.assertEqual(providers.email_route("a@b.com", "test"), ["cpaas", "zohomail"])
        with self.settings(ZOHOMAIL_REFRESH_TOKEN=""):
            self.assertEqual(providers.email_route("a@b.com", "test"), ["cpaas"])
        with self.settings(EMAIL_DELIVERY="log"):
            self.assertEqual(providers.email_route("a@b.com", "test"), ["log"])

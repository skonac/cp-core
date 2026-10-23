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
from .providers import SendError


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

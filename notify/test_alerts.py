"""notify.alerts: topics routed to Telegram channels and email. No network: urlopen and the email send are replaced."""
import io
import json
import urllib.error
from unittest import mock

from django.test import SimpleTestCase, override_settings

from . import alerts
from .providers import SendError

BASE = dict(TELEGRAM_DELIVERY="live", TELEGRAM_BOT_TOKEN="123:abc", SITE_DOMAIN="t.org",
            TELEGRAM_CHANNELS={"critical": "-100111", "info": "-100222"},
            NOTIFY_ROUTES={"images_fail": "telegram.critical", "accounts_new": "telegram.info",
                           "shop_fail": "telegram.critical, email.a@x.com, email.b@x.com", "shop_success": "none"})


def reply(body=None):
    r = mock.MagicMock()
    r.__enter__.return_value = r
    r.status = 200
    r.read.return_value = json.dumps(body or {"ok": True, "result": {"message_id": 7}}).encode()
    return r


def http_error(status, body):
    return urllib.error.HTTPError("https://x", status, "err", {}, io.BytesIO(json.dumps(body).encode()))


def posted(urlopen):
    return [json.loads(c.args[0].data) for c in urlopen.call_args_list]


@override_settings(**BASE)
class AlertTests(SimpleTestCase):
    def setUp(self):
        alerts._reset()

    def test_routes_are_read_from_the_env_lines(self):
        self.assertEqual(alerts.routes("shop.fail"), ["telegram.critical", "email.a@x.com", "email.b@x.com"])
        self.assertEqual(alerts.routes("shop.success"), [])         # none
        self.assertEqual(alerts.routes("events.new"), [])           # no line: off

    def test_topic_goes_to_its_channel(self):
        with mock.patch("urllib.request.urlopen", return_value=reply()) as urlopen:
            self.assertTrue(alerts.alert("accounts.new", "new account", wait=True))
        self.assertEqual(posted(urlopen), [{"chat_id": "-100222", "text": "[t.org] accounts.new: new account",
                                            "disable_web_page_preview": True}])

    def test_no_route_sends_nothing(self):
        with mock.patch("urllib.request.urlopen") as urlopen:
            self.assertFalse(alerts.alert("events.new", "x", wait=True))
            self.assertFalse(alerts.alert("shop.success", "x", wait=True))
        urlopen.assert_not_called()

    def test_log_delivery_sends_nothing_to_telegram(self):
        with override_settings(TELEGRAM_DELIVERY="log"), mock.patch("urllib.request.urlopen") as urlopen:
            alerts.alert("images.fail", "x", wait=True)
        urlopen.assert_not_called()

    def test_email_targets(self):
        with mock.patch("urllib.request.urlopen", return_value=reply()), \
                mock.patch.object(alerts, "_send_email") as send_email:
            self.assertTrue(alerts.alert("shop.fail", "card declined", wait=True))
        self.assertEqual([c.args for c in send_email.call_args_list],
                         [("a@x.com", "[t.org] shop.fail", "card declined"), ("b@x.com", "[t.org] shop.fail", "card declined")])

    def test_a_failing_email_does_not_stop_the_others(self):
        with mock.patch("urllib.request.urlopen", return_value=reply()) as urlopen, \
                mock.patch.object(alerts, "_send_email", side_effect=[RuntimeError("db down"), None]) as send_email:
            self.assertTrue(alerts.alert("shop.fail", "x", wait=True))
        self.assertEqual(send_email.call_count, 2)
        self.assertEqual(urlopen.call_count, 1)

    def test_unknown_topic_goes_to_critical(self):
        with mock.patch("urllib.request.urlopen", return_value=reply()) as urlopen:
            alerts.alert("typo.topic", "x", wait=True)
        self.assertEqual(posted(urlopen)[0]["chat_id"], "-100111")
        self.assertIn("unknown topic", posted(urlopen)[0]["text"])

    def test_same_key_once_per_window_per_topic(self):
        with mock.patch("urllib.request.urlopen", return_value=reply()) as urlopen:
            self.assertTrue(alerts.alert("images.fail", "a", key="k", wait=True))
            self.assertFalse(alerts.alert("images.fail", "a again", key="k", wait=True))
            self.assertTrue(alerts.alert("images.fail", "b", key="other", wait=True))
            self.assertTrue(alerts.alert("accounts.new", "c", key="k", wait=True))      # another topic, same key
        self.assertEqual(urlopen.call_count, 3)

    def test_rate_limit_per_target(self):
        limit = alerts.RATE_PER_MINUTE["telegram"]
        with mock.patch("urllib.request.urlopen", return_value=reply()) as urlopen:
            results = [alerts.alert("images.fail", f"m{i}", wait=True) for i in range(limit + 3)]
            self.assertTrue(alerts.alert("accounts.new", "other target", wait=True))    # telegram.info has its own count
        self.assertEqual(results.count(True), limit)
        self.assertEqual(urlopen.call_count, limit + 1)

    def test_failures_never_reach_the_caller(self):
        with mock.patch("urllib.request.urlopen", side_effect=urllib.error.URLError("down")):
            self.assertTrue(alerts.alert("images.fail", "x", wait=True))

    def test_upgraded_group_names_the_new_id(self):
        err = http_error(400, {"ok": False, "description": "Bad Request: group chat was upgraded to a supergroup chat",
                               "parameters": {"migrate_to_chat_id": -1003935859411}})
        with mock.patch("urllib.request.urlopen", side_effect=err):
            with self.assertRaisesMessage(SendError, "new chat id: -1003935859411"):
                alerts.send_telegram("-100111", "x")

    def test_unreachable_is_retryable_and_hides_the_token(self):
        with mock.patch("urllib.request.urlopen", side_effect=urllib.error.URLError("timed out")):
            with self.assertRaises(SendError) as cm:
                alerts.send_telegram("-100111", "x")
        self.assertTrue(cm.exception.retry)
        self.assertNotIn("123:abc", str(cm.exception))

    @override_settings(TELEGRAM_BOT_TOKEN="")
    def test_missing_token(self):
        with self.assertRaises(SendError):
            alerts.send_telegram("-100111", "x")

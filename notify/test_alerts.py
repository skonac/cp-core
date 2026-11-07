"""notify.alerts: the Telegram admin group. No network: urlopen is replaced."""
import io
import json
import urllib.error
from unittest import mock

from django.test import SimpleTestCase, override_settings

from . import alerts
from .providers import SendError

LIVE = dict(TELEGRAM_DELIVERY="live", TELEGRAM_BOT_TOKEN="123:abc", TELEGRAM_ADMIN_CHAT_ID="-100555", SITE_DOMAIN="t.org")


def reply(status=200, body=None):
    r = mock.MagicMock()
    r.__enter__.return_value = r
    r.status = status
    r.read.return_value = json.dumps(body if body is not None else {"ok": True, "result": {"message_id": 7}}).encode()
    return r


def http_error(status, body):
    return urllib.error.HTTPError("https://x", status, "err", {}, io.BytesIO(json.dumps(body).encode()))


@override_settings(**LIVE)
class AlertTests(SimpleTestCase):
    def setUp(self):
        alerts._reset()

    def test_log_mode_sends_nothing(self):
        with override_settings(TELEGRAM_DELIVERY="log"), mock.patch("urllib.request.urlopen") as urlopen:
            self.assertTrue(alerts.alert("x", wait=True))
        urlopen.assert_not_called()

    def test_live_posts_to_the_group(self):
        with mock.patch("urllib.request.urlopen", return_value=reply()) as urlopen:
            self.assertTrue(alerts.alert("worker silent", wait=True))
        req = urlopen.call_args.args[0]
        self.assertEqual(req.full_url, "https://api.telegram.org/bot123:abc/sendMessage")
        sent = json.loads(req.data)
        self.assertEqual((sent["chat_id"], sent["text"]), ("-100555", "[t.org] worker silent"))

    def test_same_key_once_per_window(self):
        with mock.patch("urllib.request.urlopen", return_value=reply()) as urlopen:
            self.assertTrue(alerts.alert("a", key="k", wait=True))
            self.assertFalse(alerts.alert("a again", key="k", wait=True))
            self.assertTrue(alerts.alert("b", key="other", wait=True))
        self.assertEqual(urlopen.call_count, 2)

    def test_rate_limit(self):
        with mock.patch("urllib.request.urlopen", return_value=reply()) as urlopen:
            results = [alerts.alert(f"m{i}", wait=True) for i in range(alerts.MAX_PER_MINUTE + 3)]
        self.assertEqual(results.count(True), alerts.MAX_PER_MINUTE)
        self.assertEqual(urlopen.call_count, alerts.MAX_PER_MINUTE)

    def test_failures_never_reach_the_caller(self):
        with mock.patch("urllib.request.urlopen", side_effect=urllib.error.URLError("down")):
            self.assertTrue(alerts.alert("x", wait=True))

    def test_upgraded_group_names_the_new_id(self):
        err = http_error(400, {"ok": False, "description": "Bad Request: group chat was upgraded to a supergroup chat",
                               "parameters": {"migrate_to_chat_id": -1003935859411}})
        with mock.patch("urllib.request.urlopen", side_effect=err):
            with self.assertRaisesMessage(SendError, "new chat id: -1003935859411"):
                alerts.send("x")

    def test_unreachable_is_retryable_and_hides_the_token(self):
        with mock.patch("urllib.request.urlopen", side_effect=urllib.error.URLError("timed out")):
            with self.assertRaises(SendError) as cm:
                alerts.send("x")
        self.assertTrue(cm.exception.retry)
        self.assertNotIn("123:abc", str(cm.exception))

    @override_settings(TELEGRAM_BOT_TOKEN="")
    def test_missing_settings(self):
        with self.assertRaises(SendError):
            alerts.send("x")

"""worker.silent: images wait and no worker polls (worker/queue.py check_silent)."""
import tempfile
from datetime import timedelta
from unittest import mock

from django.test import TestCase, override_settings
from django.utils import timezone

from accounts.models import User
from common.testing import jpeg, make_event
from media.ingest import ingest_photo
from media.models import Image

from . import queue
from .models import Worker


@override_settings(MEDIA_ROOT=tempfile.mkdtemp(prefix="cp-worker-test-"))
class SilentWorkerTests(TestCase):
    def setUp(self):
        owner = User.objects.create_user("+905550000071", "pw", is_organizer=True, credit_balance=10)
        self.image = ingest_photo(make_event(owner), owner, jpeg())
        queue._last_silent_check = None

    def waiting_for(self, minutes):
        Image.objects.filter(pk=self.image.pk).update(created_at=timezone.now() - timedelta(minutes=minutes))

    def check(self):
        with mock.patch("worker.queue.alert") as alert:
            queue.check_silent()
        return alert

    def test_waiting_and_no_worker_alerts(self):
        self.waiting_for(10)
        alert = self.check()
        self.assertEqual(alert.call_args.args[0], "worker.silent")
        self.assertIn("1 image(s) waiting", alert.call_args.args[1])
        self.assertIn("no worker has ever polled", alert.call_args.args[1])

    def test_a_polling_worker_means_no_alert(self):
        self.waiting_for(10)
        Worker.objects.create(worker_id="w1", last_seen_at=timezone.now() - timedelta(seconds=30))
        self.check().assert_not_called()

    def test_a_silent_worker_alerts_with_its_age(self):
        self.waiting_for(10)
        Worker.objects.create(worker_id="w1", last_seen_at=timezone.now() - timedelta(minutes=20))
        self.assertIn("no worker polled for 20 min", self.check().call_args.args[1])

    def test_a_fresh_image_is_not_late_yet(self):
        self.waiting_for(1)
        self.check().assert_not_called()

    def test_checked_at_most_every_five_minutes(self):
        self.waiting_for(10)
        self.assertEqual(self.check().call_count, 1)
        self.assertEqual(self.check().call_count, 0)       # throttled: the database is not even asked

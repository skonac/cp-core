"""Staff pages: every page renders for staff, nobody else gets in, the "as user" view shows that user's matches."""
import io
import math
import random
import tempfile

from django.test import TestCase, override_settings
from PIL import Image as PILImage

from accounts.models import User
from events.models import Event, EventMember
from media.ingest import ingest_photo, ingest_selfie
from worker import queue

ENGINE = "yunet-2023mar+sface-2021dec/1400/0.8"


def _jpeg(color) -> bytes:
    buf = io.BytesIO()
    PILImage.new("RGB", (800, 600), color).save(buf, "JPEG")
    return buf.getvalue()


def _unit(v):
    n = math.sqrt(sum(x * x for x in v))
    return [x / n for x in v]


def _face(embedding, x=0.4):
    return {"bbox": [x, 0.2, x + 0.1, 0.35], "score": 0.9, "embedding": embedding,
            "landmarks": [[x + 0.03, 0.25], [x + 0.07, 0.25], [x + 0.05, 0.28], [x + 0.03, 0.31], [x + 0.07, 0.31]]}


@override_settings(MEDIA_ROOT=tempfile.mkdtemp(prefix="cp-staff-test-"))
class StaffPagesTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        rnd = random.Random(1)
        vec = lambda: _unit([rnd.gauss(0, 1) for _ in range(128)])   # noqa: E731
        me = vec()
        cls.staff = User.objects.create_superuser("+905550000009", "pw")
        cls.org = User.objects.create_user("+905550000001", "pw", is_organizer=True, credit_balance=10)
        cls.guest = User.objects.create_user("+905550000002", "pw", is_participant=True, kvkk_version="001")
        cls.event = Event.objects.create(name="Test", date="2026-10-19", owner=cls.org)
        EventMember.objects.create(user=cls.guest, event=cls.event)
        for color in ("red", "green", "blue"):
            ingest_photo(cls.event, cls.org, _jpeg(color))
        ingest_selfie(cls.guest, _jpeg("white"), "primary")
        results = [  # claim order: the selfie first, then photos 1, 2, 3
            {"faces": [_face(me)], "faces_found": 1},
            {"faces": [_face(me), _face(vec(), 0.6)]},     # photo 1: the guest is in it
            {"faces": [_face(vec())]},                     # photo 2: someone else
            {"error": "unreadable_image", "detail": "test"},
        ]
        for body in results:
            image = queue.claim("test-1")
            if "error" not in body:
                body = {"engine": ENGINE, "width": 800, "height": 600, "ms": 1, **body}
            queue.store_result(image.id, body)
        cls.selfie_id = cls.guest.images.get(is_selfie=True).id

    def setUp(self):
        self.client.login(phone="+905550000009", password="pw")

    def test_pages_render(self):
        code = self.event.code
        photo_id = self.event.images.order_by("seq").first().id
        for url in ["/dj/staff/", "/dj/staff/events/", f"/dj/staff/events/{code}/",
                    f"/dj/staff/events/{code}/?status=failed&deleted=1",
                    f"/dj/staff/images/{photo_id}/", f"/dj/staff/images/{self.selfie_id}/", "/dj/staff/users/",
                    "/dj/staff/users/?q=0002", f"/dj/staff/users/{self.guest.id}/", f"/dj/staff/users/{self.org.id}/",
                    "/dj/staff/worker/", "/admin/", f"/admin/accounts/user/{self.guest.id}/change/"]:
            with self.subTest(url=url):
                self.assertEqual(self.client.get(url).status_code, 200)

    def test_files(self):
        photo = self.event.images.order_by("seq").first()
        for r in ("t", "d", "m"):
            self.assertEqual(self.client.get(f"/dj/staff/images/{photo.id}/{r}.jpg").status_code, 200)
        self.assertEqual(self.client.get(f"/dj/staff/images/{photo.id}/x.jpg").status_code, 404)

    def test_as_user_shows_only_their_photos(self):
        r = self.client.get(f"/dj/staff/events/{self.event.code}/?as={self.guest.id}")
        self.assertEqual([img.seq for img in r.context["page"]], [1])
        self.assertGreater(r.context["page"][0].score, 0.99)

    def test_failed_listed(self):
        r = self.client.get("/dj/staff/worker/")
        self.assertEqual([i.seq for i in r.context["failed"]], [3])

    def test_only_staff(self):
        self.client.logout()
        self.assertRedirects(self.client.get("/dj/staff/"), "/dj/login/?next=/dj/staff/", fetch_redirect_response=False)
        for phone in ("+905550000001", "+905550000002"):
            self.client.login(phone=phone, password="pw")
            self.assertEqual(self.client.get("/dj/staff/users/").status_code, 403)

    def test_no_groups_in_admin(self):
        self.assertEqual(self.client.get("/admin/auth/group/").status_code, 404)
        self.assertTrue(self.staff.has_perm("events.change_event"))
        self.assertFalse(self.org.has_perm("events.change_event"))

"""Frames on photos (media/framing.py): framed display/download/ZIP, lazy rendering, invalidation."""
import io
import os
import zipfile

from django.test import Client, TestCase
from PIL import Image as PILImage

from accounts.models import User
from common.testing import TempMedia, jpeg, make_event
from media.framing import framed_paths, served_relpath
from media.ingest import delete_frame, ingest_frame, ingest_photo
from media.storage import abspath, soft_delete

RED, BLUE = (255, 0, 0, 255), (0, 0, 255, 255)


def frame_png(size, color=RED, border=100) -> bytes:
    """Transparent in the middle, an opaque border of `color`."""
    im = PILImage.new("RGBA", size, color)
    im.paste((0, 0, 0, 0), (border, border, size[0] - border, size[1] - border))
    buf = io.BytesIO()
    im.save(buf, "PNG")
    return buf.getvalue()


def pixels(data: bytes) -> tuple:
    with PILImage.open(io.BytesIO(data)) as im:
        im = im.convert("RGB")
        return im.size, im.getpixel((3, 3)), im.getpixel((im.width // 2, im.height // 2))


def near(color, want, tolerance=40) -> bool:
    return all(abs(a - b) <= tolerance for a, b in zip(color, want))


def body(r) -> bytes:
    return b"".join(r.streaming_content) if r.streaming else r.content


class FrameRenderingTests(TempMedia, TestCase):
    def setUp(self):
        self.owner = User.objects.create_user("+905550001001", is_organizer=True, credit_balance=100)
        self.event = make_event(self.owner)
        self.wide = ingest_photo(self.event, self.owner, jpeg(color=(0, 200, 0), size=(2400, 1600), seed=1))
        self.tall = ingest_photo(self.event, self.owner, jpeg(color=(0, 200, 0), size=(1600, 2400), seed=2))
        self.client = Client()
        self.client.force_login(self.owner)

    def _display(self, image):
        r = self.client.get(f"/api/organizer/events/{self.event.code}/photos/{image.seq_str}/display.jpg")
        self.assertEqual(r.status_code, 200)
        return pixels(body(r))

    def test_framed_display_lazy_and_by_orientation(self):
        size, corner, middle = self._display(self.wide)                     # no frame yet: plain
        self.assertTrue(near(corner, (0, 200, 0)))
        ingest_frame(self.event, self.owner, frame_png((3000, 2000)), "landscape")
        self.assertEqual(framed_paths(self.event), [])                     # nothing rendered until asked
        size, corner, middle = self._display(self.wide)
        self.assertTrue(near(corner, RED[:3]) and near(middle, (0, 200, 0)), (corner, middle))
        self.assertTrue(abspath(self.wide.relpath("fd")).is_file())
        self.assertFalse(near(self._display(self.tall)[1], RED[:3]))       # portrait slot empty: plain
        plain = PILImage.open(abspath(self.wide.relpath("d")))
        self.assertTrue(near(plain.convert("RGB").getpixel((3, 3)), (0, 200, 0)))   # the source stays unframed
        self.assertEqual(plain.size, size)                                 # same size: face boxes still valid

    def test_change_remove_and_stale(self):
        ingest_frame(self.event, self.owner, frame_png((3000, 2000)), "landscape")
        self.event.refresh_from_db()
        self._display(self.wide)
        with self.captureOnCommitCallbacks(execute=True):
            ingest_frame(self.event, self.owner, frame_png((3000, 2000), BLUE), "landscape")
        self.assertFalse(abspath(self.wide.relpath("fd")).is_file())       # change: framed files dropped
        self.assertTrue(near(self._display(self.wide)[1], BLUE[:3]))
        # A framed file older than frame_changed_at (a render that raced a change) counts as missing.
        path = abspath(self.wide.relpath("fd"))
        path.write_bytes(jpeg(color=(255, 255, 0), size=(1600, 1067)))
        self.event.refresh_from_db()
        old = self.event.frame_changed_at.timestamp() - 60
        os.utime(path, (old, old))
        self.assertTrue(near(self._display(self.wide)[1], BLUE[:3]))
        self.event.refresh_from_db()
        with self.captureOnCommitCallbacks(execute=True):
            delete_frame(self.event, "landscape")
        self.assertEqual(framed_paths(self.event), [])
        self.assertTrue(near(self._display(self.wide)[1], (0, 200, 0)))

    def test_download_zip_and_delete(self):
        ingest_frame(self.event, self.owner, frame_png((2000, 3000)), "portrait")
        self.event.refresh_from_db()
        self.assertEqual(served_relpath(self.tall, "m", self.event), self.tall.relpath("fm"))
        self.assertEqual(served_relpath(self.tall, "t", self.event), self.tall.relpath("t"))   # thumbs never framed
        self.assertEqual(served_relpath(self.wide, "m", self.event), self.wide.relpath("m"))
        size, corner, _ = pixels(abspath(self.tall.relpath("fm")).read_bytes())
        self.assertEqual((size, near(corner, RED[:3])), ((self.tall.width, self.tall.height), True))
        r = self.client.get(f"/api/events/{self.event.code}/download.zip", {"all": "true", "view": "all"})
        self.assertEqual(r.status_code, 200)                               # the owner, as a member
        with zipfile.ZipFile(io.BytesIO(body(r))) as zf:
            corners = sorted(near(pixels(zf.read(n))[1], RED[:3]) for n in zf.namelist())
        self.assertEqual(corners, [False, True])                           # wide plain, tall framed
        r = self.client.get(f"/api/events/{self.event.code}/photos/{self.tall.seq_str}/download")
        self.assertTrue(near(pixels(body(r))[1], RED[:3]))
        fm = abspath(self.tall.relpath("fm"))
        with self.captureOnCommitCallbacks(execute=True):
            soft_delete(self.tall)
        self.assertFalse(fm.exists())                                      # framed files go with the photo

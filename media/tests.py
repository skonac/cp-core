"""Upload pipeline (media/ingest.py), the bulk import command, matching with "not me" rejections."""
import io
import math
import random
import tempfile
from pathlib import Path

from django.core.management import call_command
from django.test import TestCase
from PIL import ExifTags, Image as PILImage
from PIL.TiffImagePlugin import IFDRational

from accounts.models import User
from common.testing import TempMedia, add_photo, add_selfie, face_of, jpeg, make_event, person
from media.ingest import ingest_photo
from media.matching import is_my_photo, my_matches, my_photos
from media.models import FaceRejection, Image


def jpeg_with_unknown_lens_aperture() -> bytes:
    """EXIF LensSpecification with 0/0 rationals ("unknown"), as some cameras write it.
    Pillow reads 0/0 as NaN, which JSON (and Postgres jsonb) cannot hold. Bug of Oct 19, 2026."""
    im = PILImage.new("RGB", (300, 200), (100, 120, 140))
    exif = im.getexif()
    exif.get_ifd(ExifTags.IFD.Exif)[0xA432] = (IFDRational(24, 1), IFDRational(70, 1),
                                               IFDRational(0, 0), IFDRational(0, 0))
    buf = io.BytesIO()
    im.save(buf, "JPEG", exif=exif)
    return buf.getvalue()


class ExifTests(TempMedia, TestCase):
    def setUp(self):
        self.owner = User.objects.create_user("+905550000001", credit_balance=10)
        self.event = make_event(self.owner)

    def test_nan_exif_value_stored_as_null(self):
        image = ingest_photo(self.event, self.owner, jpeg_with_unknown_lens_aperture())
        image.refresh_from_db()
        self.assertEqual(image.exif["LensSpecification"], [24.0, 70.0, None, None])

    def test_import_command_survives_odd_exif(self):
        with tempfile.TemporaryDirectory() as folder:
            Path(folder, "a.jpg").write_bytes(jpeg(seed=1))
            Path(folder, "b_lens.jpg").write_bytes(jpeg_with_unknown_lens_aperture())
            Path(folder, "c.jpg").write_bytes(jpeg(seed=3))
            out = io.StringIO()
            call_command("import_photos", folder, event=self.event.code, stdout=out)
        self.assertIn("3/3 accepted", out.getvalue())
        self.assertEqual(Image.objects.filter(event=self.event).count(), 3)
        self.owner.refresh_from_db()
        self.assertEqual(self.owner.credit_balance, 7)


def relative_of(identity: list[float], similarity: float, seed: int) -> list[float]:
    """Another person who looks like `identity`: cosine similarity `similarity` to it (a parent, a sibling)."""
    rnd = random.Random(seed)
    other = [rnd.gauss(0, 1) for _ in range(128)]
    dot = sum(a * b for a, b in zip(other, identity))
    other = [a - dot * b for a, b in zip(other, identity)]            # orthogonal to identity
    n = math.sqrt(sum(a * a for a in other))
    k = math.sqrt(1 - similarity ** 2)
    return [similarity * b + k * a / n for a, b in zip(other, identity)]


class RejectionMatchingTests(TempMedia, TestCase):
    """The father case: his faces match the son's selfie (a relative, above the threshold), and no
    threshold separates them from the son's weaker photos. One "not me" on one of the father's faces
    hides his other photos and keeps the son's."""

    def setUp(self):
        self.owner = User.objects.create_user("+905550000001", credit_balance=100)
        self.son = User.objects.create_user("+905550000002", kvkk_version="002")
        self.event = make_event(self.owner)
        me = person(1)
        father = relative_of(me, 0.55, seed=7)
        add_selfie(self.son, me)
        self.mine = [add_photo(self.event, i, [face_of(me, i, noise=0.12)]) for i in range(1, 5)]   # sim ~0.55
        self.his = [add_photo(self.event, 10 + i, [face_of(father, 10 + i, noise=0.06)]) for i in range(4)]
        self.both = add_photo(self.event, 20, [face_of(father, 20, noise=0.06), face_of(me, 21, noise=0.12)])

    def reject(self, image, index=0):
        face = list(image.faces.order_by("id"))[index]
        return FaceRejection.objects.create(user=self.son, face=face, event=image.event)

    def test_one_rejection_hides_his_photos_keeps_mine(self):
        before = my_matches(self.son, self.event)
        self.assertEqual(set(before), {p.pk for p in self.mine + self.his + [self.both]})   # all above 0.363
        weakest_mine = min(before[p.pk].score for p in self.mine)
        strongest_his = max(before[p.pk].score for p in self.his)
        self.assertLess(weakest_mine, strongest_his)   # overlap: no threshold keeps all mine and drops all his
        self.reject(self.his[0])
        after = my_matches(self.son, self.event)
        self.assertEqual(set(after), {p.pk for p in self.mine + [self.both]})
        # the photo with both of us stays, now framed on my face
        mine_in_both = list(self.both.faces.order_by("id"))[1]
        self.assertEqual(after[self.both.pk].face_id, mine_in_both.pk)
        self.assertFalse(is_my_photo(self.son, self.his[1]))       # file access follows
        self.assertTrue(is_my_photo(self.son, self.both))

    def test_rejections_count_in_other_events(self):
        other = make_event(self.owner)
        his_there = add_photo(other, 30, [list(self.his[0].faces.get().embedding)])
        self.reject(self.his[0])
        self.assertEqual(my_photos(self.son, other), {})
        self.assertEqual(len(my_photos(self.son, self.event)), 5)
        his_there.delete()

    def test_deleting_the_photo_removes_the_rejection(self):
        self.reject(self.his[0])
        self.his[0].faces.all().delete()            # what deleting a photo does to its faces
        self.assertEqual(FaceRejection.objects.count(), 0)
        self.assertEqual(len(my_photos(self.son, self.event)), 8)

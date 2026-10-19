"""Upload pipeline (media/ingest.py) and the bulk import command."""
import io
import tempfile
from pathlib import Path

from django.core.management import call_command
from django.test import TestCase
from PIL import ExifTags, Image as PILImage
from PIL.TiffImagePlugin import IFDRational

from accounts.models import User
from common.testing import TempMedia, jpeg, make_event
from media.ingest import ingest_photo
from media.models import Image


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

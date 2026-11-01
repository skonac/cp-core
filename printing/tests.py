"""Print themes: discovery, the filled HTML, the PDF (WeasyPrint, fonts and images read from disk), the public
asset view, the staff page. design/core.md "Printed materials"."""
import tempfile

from django.test import TestCase, override_settings

from accounts.models import User
from common.testing import jpeg, make_event
from media.ingest import ingest_cover

from . import render as R
from . import themes


@override_settings(MEDIA_ROOT=tempfile.mkdtemp(prefix="cp-print-test-"))
class PrintTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.staff = User.objects.create_superuser("+905550000009", "pw")
        cls.org = User.objects.create_user("+905550000001", "pw", is_organizer=True)
        cls.event = make_event(cls.org, name="Derya & Can", city="İstanbul", venue="Çırağan Sarayı")

    def test_themes_found_with_their_formats(self):
        theme = themes.get("elegant-wedding")
        self.assertEqual(theme.formats, ["poster", "table"])
        self.assertEqual(theme.name["tr"], "Zarif Düğün")
        self.assertIn("elegant-wedding", [t.id for t in themes.all_themes()])
        self.assertIsNone(themes.get("_fonts"))

    def test_html_has_the_event_qr_and_fallback_photo(self):
        theme = themes.get("elegant-wedding")
        page = R.render_html(theme, "poster", self.event, "tr", "https://cleverphotos.org/?event=RK238", None)
        self.assertIn('lang="tr"', page)
        self.assertIn("Derya &amp; Can", page)          # escaped
        self.assertIn("ÇIRAĞAN SARAYI", page)             # shown in capitals, Turkish rules
        self.assertIn('class="qr-code"', page)
        self.assertIn("@page { size: 420mm 594mm", page)
        self.assertIn("/api/print/assets/elegant-wedding/images/fallback.jpg", page)
        self.assertIn("/api/print/assets/_fonts/fonts.css", page)
        en = R.render_html(theme, "table", self.event, "en", "https://x/?event=RK238", "/cover.jpg")
        self.assertIn("WELCOME", en)
        self.assertIn('src="/cover.jpg"', en)
        self.assertIn("@page { size: 74mm 210mm", en)

    def test_caps_turkish(self):
        from .textfilters import caps
        self.assertEqual(caps("Hoş geldiniz", "tr"), "HOŞ GELDİNİZ")
        self.assertEqual(caps("Kırıkkale", "tr"), "KIRIKKALE")
        self.assertEqual(caps("Welcome, Liz", "en"), "WELCOME, LIZ")
        page = R.render_html(themes.get("elegant-wedding"), "poster", self.event, "tr", "https://x/?event=X", None)
        self.assertIn("DÜĞÜNÜMÜZE HOŞ GELDİNİZ", page)

    def test_corporate_theme_and_staff_samples(self):
        theme = themes.get("corporate-modern")
        self.assertEqual(theme.formats, ["poster", "table"])
        self.assertEqual([t.id for t in themes.all_themes()][:3], ["elegant-wedding", "corporate-modern", "celebration"])
        self.client.force_login(self.staff)
        r = self.client.get("/dj/staff/print/corporate-modern/table.html?event=short")
        self.assertContains(r, "Norda 2027 Kick-off")                                   # the theme's own sample
        self.assertContains(r, "/api/print/assets/corporate-modern/images/sample-logo.jpg")   # sample cover
        r = self.client.get("/dj/staff/print/corporate-modern/poster.pdf?event=long")
        self.assertEqual(r["Content-Type"], "application/pdf")
        page = R.render_html(theme, "poster", self.event, "tr", "https://x/?event=X", None)
        self.assertIn('class="band no-logo"', page)                                     # no cover: no logo panel

    def test_every_theme_renders_both_formats_and_languages(self):
        for theme in themes.all_themes():
            for fmt in theme.formats:
                for lang in themes.LANGS:
                    with self.subTest(theme=theme.id, fmt=fmt, lang=lang):
                        page = R.render_html(theme, fmt, self.event, lang, "https://x/?event=X", None)
                        self.assertIn('class="qr-code"', page)
                        self.assertNotIn("{{", page)

    def test_name_size_and_date(self):
        from datetime import date
        self.assertEqual([R.name_size("x" * n) for n in (14, 15, 23, 60)], ["s", "m", "l", "xl"])
        self.assertEqual(R.format_date(date(2026, 9, 12), "tr"), "12 Eylül 2026")
        self.assertEqual(R.format_date(date(2026, 9, 12), "en"), "12 September 2026")

    def test_pdf_reads_fonts_and_cover_from_disk(self):
        ingest_cover(self.event, self.org, jpeg(size=(1200, 900)))
        self.event.refresh_from_db()
        cover_file = R.cover_path(self.event)
        self.assertIsNotNone(cover_file)
        theme = themes.get("elegant-wedding")
        page = R.render_html(theme, "poster", self.event, "tr", "https://cleverphotos.org/?event=X", "/c.jpg")
        fetch = R._fetcher("/c.jpg", cover_file)
        self.assertEqual(fetch("https://print.invalid/c.jpg")["mime_type"], "image/jpeg")
        self.assertEqual(fetch(R.PDF_BASE + "api/print/assets/_fonts/jost-300.woff2")["mime_type"], "font/woff2")
        for refused in ("https://example.com/x.png", R.PDF_BASE + "api/print/assets/../../manage.py",
                        "file:///etc/passwd"):
            with self.subTest(refused), self.assertRaises(ValueError):
                fetch(refused)
        pdf = R.render_pdf(page, "/c.jpg", cover_file)
        self.assertTrue(pdf.startswith(b"%PDF"))
        self.assertGreater(len(pdf), 20_000)            # fonts and the cover embedded

    def test_render_busy(self):
        with override_settings(), self.settings():
            for _ in range(2):
                R._renders.acquire()
            try:
                with self.assertRaises(R.RenderBusy):
                    R.render_pdf("<p>x</p>")
            finally:
                R._renders.release(); R._renders.release()

    def test_asset_view(self):
        r = self.client.get("/api/print/assets/_fonts/fonts.css")
        self.assertEqual((r.status_code, r["Content-Type"]), (200, "text/css"))
        self.assertEqual(self.client.get("/api/print/assets/elegant-wedding/images/fallback.jpg").status_code, 200)
        for path in ("elegant-wedding/theme.json", "elegant-wedding/poster.html", "../manage.py", "nope.css"):
            with self.subTest(path):
                self.assertEqual(self.client.get(f"/api/print/assets/{path}").status_code, 404)

    def test_staff_pages(self):
        self.client.force_login(self.org)
        self.assertEqual(self.client.get("/dj/staff/print/").status_code, 403)
        self.client.force_login(self.staff)
        r = self.client.get("/dj/staff/print/")
        self.assertContains(r, "Zarif Düğün")
        self.assertContains(r, f"event={self.event.display_code}")
        r = self.client.get("/dj/staff/print/elegant-wedding/table.html?event=long&lang=en")
        self.assertContains(r, "Ayşe Nur Yılmaz")
        self.assertEqual(r["X-Frame-Options"], "SAMEORIGIN")
        r = self.client.get(f"/dj/staff/print/elegant-wedding/poster.pdf?event={self.event.display_code}")
        self.assertEqual(r["Content-Type"], "application/pdf")
        self.assertIn(f'{self.event.display_code}-elegant-wedding-poster.pdf', r["Content-Disposition"])
        self.assertEqual(self.client.get("/dj/staff/print/elegant-wedding/flyer.html").status_code, 404)
        self.assertEqual(self.client.get("/dj/staff/print/nope/poster.html").status_code, 404)

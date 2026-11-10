"""Operation mode (common/mode.py): the switch, what each mode blocks, the staff page, the command."""
from io import StringIO

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import Client, TestCase

from accounts.models import User
from common import mode as m
from common.models import OperationMode


class OperationModeTests(TestCase):
    def setUp(self):
        m._reset()
        self.addCleanup(m._reset)          # never leak a cached lockdown into other tests
        self.user = User.objects.create_user("+905550000011", password="pw-user-1")
        self.staff = User.objects.create_user("+905550000012", password="pw-staff-1", is_staff=True)
        self.anon = Client()
        self.as_user = Client(); self.as_user.force_login(self.user)
        self.as_staff = Client(); self.as_staff.force_login(self.staff)

    def code(self, r):
        return (r.status_code, r.json().get("code") if r["Content-Type"].startswith("application/json") else None)

    # --- the switch

    def test_default_is_normal_and_changes_apply_at_once(self):
        self.assertEqual(m.current(), "normal")
        m.set_mode("restricted", "testing", by=self.staff)
        self.assertEqual(m.current(), "restricted")
        self.assertEqual(self.anon.get("/api/operation-mode").json()["data"], {"mode": "restricted"})
        row = m.latest()
        self.assertEqual((row.mode, row.reason, row.changed_by), ("restricted", "testing", self.staff))

    def test_reason_required_except_for_normal(self):
        with self.assertRaises(ValueError):
            m.set_mode("lockdown", "  ")
        with self.assertRaises(ValueError):
            m.set_mode("sideways", "x")
        m.set_mode("lockdown", "x")
        row = m.set_mode("normal", "ignored")
        self.assertEqual(row.reason, "")
        self.assertEqual(OperationMode.objects.count(), 2)

    def test_other_process_follows_after_cache(self):
        m.current()                                            # cached: normal
        OperationMode.objects.create(mode="lockdown", reason="from elsewhere")
        self.assertEqual(m.current(), "normal")                # still the cached value
        m._cached = (m._cached[0], 0.0)                         # cache expired
        self.assertEqual(m.current(), "lockdown")

    # --- restricted

    def test_restricted_blocks_starting_a_purchase_only(self):
        m.set_mode("restricted", "payments bug")
        self.assertEqual(self.code(self.as_user.post("/api/shop/purchases", {}, content_type="application/json")),
                         (503, "payments_disabled"))
        self.assertEqual(self.code(self.anon.get("/api/shop/products"))[0], 200)
        self.assertEqual(self.code(self.as_user.get("/api/me"))[0], 200)
        # staff pass (whatever the endpoint then says about the empty body)
        self.assertNotEqual(self.as_staff.post("/api/shop/purchases", {}, content_type="application/json").status_code, 503)

    # --- lockdown

    def test_lockdown_blocks_everyone_but_staff(self):
        m.set_mode("lockdown", "incident")
        self.assertEqual(self.code(self.anon.get("/api/public/event-types")), (503, "site_locked"))
        self.assertEqual(self.code(self.as_user.get("/api/me")), (503, "site_locked"))
        r = self.as_user.get("/dj/me/")
        self.assertEqual(r.status_code, 503)
        self.assertIn("geçici olarak kapalı", r.content.decode())
        self.assertEqual(r["Retry-After"], "300")
        self.assertEqual(self.as_staff.get("/api/me").status_code, 200)
        self.assertEqual(self.as_staff.get("/dj/staff/").status_code, 200)

    def test_lockdown_keeps_the_service_paths_open(self):
        m.set_mode("lockdown", "incident")
        self.assertEqual(self.code(self.anon.get("/api/health"))[0], 200)
        self.assertEqual(self.anon.get("/api/operation-mode").json()["data"], {"mode": "lockdown"})
        self.assertEqual(self.anon.get("/dj/login/").status_code, 200)
        for path in ("/api/faceworker/jobs/next", "/api/shop/iyzico/webhook", "/api/shop/iyzico/callback",
                     "/admin/"):
            self.assertNotEqual(self.anon.get(path).status_code, 503, path)

    # --- staff page

    def test_staff_panel(self):
        self.assertEqual(self.as_user.get("/dj/staff/mode/").status_code, 403)
        self.assertEqual(self.as_user.post("/dj/staff/mode/", {"mode": "lockdown", "reason": "x"}).status_code, 403)
        for page in ("/dj/staff/", "/dj/staff/mode/"):          # the panel: index and its own page
            r = self.as_staff.get(page)
            self.assertContains(r, "Now: <b>NORMAL</b>")
            self.assertContains(r, m.DESCRIPTIONS["lockdown"])

        r = self.as_staff.post("/dj/staff/mode/", {"mode": "lockdown", "reason": "", "next": "/dj/staff/"}, follow=True)
        self.assertRedirects(r, "/dj/staff/")
        self.assertContains(r, "Not changed: a reason is required")
        self.assertEqual(m.current(), "normal")

        r = self.as_staff.post("/dj/staff/mode/", {"mode": "lockdown", "reason": "double charges", "next": "/dj/staff/"},
                               follow=True)
        self.assertRedirects(r, "/dj/staff/")
        self.assertContains(r, "Operation mode is now LOCKDOWN.")
        self.assertContains(r, "Now: <b style")                           # the panel, red
        self.assertContains(self.as_staff.get("/dj/staff/users/"), "Operation mode: <b>LOCKDOWN</b>")   # banner elsewhere
        self.assertEqual(m.current(), "lockdown")
        self.assertContains(self.as_staff.get("/dj/staff/mode/"), "double charges")   # history

        r = self.as_staff.post("/dj/staff/mode/", {"mode": "normal", "reason": "ignored", "next": "https://evil.example/"})
        self.assertRedirects(r, "/dj/staff/mode/", fetch_redirect_response=False)   # no off-site redirect
        self.assertEqual((m.current(), m.latest().reason), ("normal", ""))

    # --- command

    def test_command(self):
        out = StringIO()
        call_command("operation_mode", "restricted", "from ssh", stdout=out)
        self.assertEqual((m.current(), m.latest().changed_by), ("restricted", None))
        out = StringIO()
        call_command("operation_mode", stdout=out)
        self.assertIn("now: restricted", out.getvalue())
        self.assertIn("from ssh", out.getvalue())
        with self.assertRaises(CommandError):
            call_command("operation_mode", "lockdown", stdout=StringIO())

    # --- what the UI reads

    def test_ui_fields(self):
        products = lambda c: c.get("/api/shop/products").json()["data"]["paymentsEnabled"]
        mode_in_me = lambda c: c.get("/api/me").json()["data"]["operationMode"]
        self.assertEqual((products(self.anon), products(self.as_user), mode_in_me(self.as_user)), (True, True, "normal"))
        m.set_mode("restricted", "x")
        self.assertEqual((products(self.anon), products(self.as_user), products(self.as_staff)), (False, False, True))
        self.assertEqual(mode_in_me(self.as_user), "restricted")
        m.set_mode("lockdown", "x")
        self.assertEqual(mode_in_me(self.as_staff), "lockdown")   # staff get the banner; others get 503

"""Event codes."""
from django.test import TestCase

from .models import Event


class EventCodeLengthTests(TestCase):
    def test_new_codes_are_six_and_old_ones_still_work(self):
        from django.test import Client
        from accounts.models import User
        from events.apps import check_code_length
        owner = User.objects.create_user("+905550000801", is_organizer=True)
        new = Event.objects.create(owner=owner, name="New", date="2026-11-07")
        old = Event.objects.create(owner=owner, name="Old", date="2026-11-07", code="rk238")
        self.assertEqual(len(new.code), 6)
        c = Client()
        for e in (new, old):
            self.assertEqual(c.get(f"/api/public/events/{e.display_code}").status_code, 200)
        self.assertEqual(check_code_length(), [])
        with self.settings(EVENT_CODE_LENGTH=9):
            self.assertEqual([e.id for e in check_code_length()], ["cp.E030"])

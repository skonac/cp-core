"""Upload a selfie from the shell (same accept step as the web upload).

    manage.py import_selfie +905551234567 me.jpg --consent [--slot secondary]
"""
from pathlib import Path

from django.core.management.base import BaseCommand

from accounts.models import User
from media.ingest import IngestError, ingest_selfie


class Command(BaseCommand):
    help = "Import a selfie for a user (created if missing)."

    def add_arguments(self, parser):
        parser.add_argument("phone")
        parser.add_argument("file")
        parser.add_argument("--slot", default="primary", choices=["primary", "secondary"])
        parser.add_argument("--consent", action="store_true", help="record consent first (testing)")

    def handle(self, phone, file, slot, consent, **_):
        user = User.objects.filter(phone=phone).first() or User.objects.create_user(phone, is_participant=True)
        if consent and not user.is_consent:
            user.is_consent = True
            user.save(update_fields=["is_consent"])
        try:
            img = ingest_selfie(user, Path(file).read_bytes(), slot)
        except IngestError as e:
            self.stdout.write(self.style.WARNING(f"rejected: {e.code} {e.detail}"))
            return
        self.stdout.write(f"selfie {img.pk} stored for {user} ({slot}), waiting for the worker")

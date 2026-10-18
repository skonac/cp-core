"""Upload photos from the shell, through the same accept step as the web upload (credits included).

    manage.py import_photos --event rk238 ~/photos/
    manage.py import_photos --new "Test wedding" --owner +905551234567 ~/photos/*.jpg
"""
import datetime
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError

from accounts.models import User
from events.codes import normalize_code
from events.models import Event
from media.ingest import IngestError, ingest_photo

SUFFIXES = {".jpg", ".jpeg", ".png", ".webp", ".heic", ".heif"}


class Command(BaseCommand):
    help = "Import photo files or folders into an event."

    def add_arguments(self, parser):
        parser.add_argument("paths", nargs="+")
        parser.add_argument("--event", help="event code")
        parser.add_argument("--new", metavar="NAME", help="create a new event with this name")
        parser.add_argument("--owner", metavar="PHONE", help="owner of the new event (made organizer)")

    def handle(self, paths, event, new, owner, **_):
        if new:
            if not owner:
                raise CommandError("--new needs --owner")
            user = User.objects.filter(phone=owner).first() or User.objects.create_user(owner)
            if not user.is_organizer:
                user.is_organizer = True
                user.save(update_fields=["is_organizer"])
            ev = Event.objects.create(name=new, owner=user, date=datetime.date.today())
            self.stdout.write(f"created event {ev.display_code} '{ev.name}' owned by {user}")
        elif event:
            ev = Event.objects.filter(code=normalize_code(event)).first()
            if not ev:
                raise CommandError(f"no event {event}")
        else:
            raise CommandError("give --event CODE or --new NAME --owner PHONE")

        files = []
        for p in map(Path, paths):
            files += sorted(f for f in p.iterdir() if f.suffix.lower() in SUFFIXES) if p.is_dir() else [p]
        ok = 0
        for f in files:
            try:
                img = ingest_photo(ev, ev.owner, f.read_bytes())
                ok += 1
                self.stdout.write(f"{img.seq_str}  {f.name}  {img.width}x{img.height}  taken {img.taken_at or '-'}")
            except IngestError as e:
                self.stdout.write(self.style.WARNING(f"-----  {f.name}  {e.code} {e.detail}"))
        ev.owner.refresh_from_db()
        self.stdout.write(f"{ok}/{len(files)} accepted into {ev.display_code}; "
                          f"owner balance {ev.owner.credit_balance}")

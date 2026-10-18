"""Print a user's matched photos in an event (matching computed on read).

    manage.py show_matches rk238 +905551234567 [--threshold 0.3]
"""
from django.core.management.base import BaseCommand, CommandError

from accounts.models import User
from events.codes import normalize_code
from events.models import Event
from media.matching import my_photos, user_faces
from media.models import Image


class Command(BaseCommand):
    def add_arguments(self, parser):
        parser.add_argument("event")
        parser.add_argument("phone")
        parser.add_argument("--threshold", type=float)

    def handle(self, event, phone, threshold, **_):
        ev = Event.objects.filter(code=normalize_code(event)).first()
        user = User.objects.filter(phone=phone).first()
        if not ev or not user:
            raise CommandError("unknown event or user")
        photos = Image.objects.filter(event=ev, deleted_at__isnull=True)
        self.stdout.write(f"event {ev.display_code}: {photos.count()} photos, "
                          f"{photos.filter(status='done').count()} processed; "
                          f"{user}: {user_faces(user).count()} ready selfie face(s)")
        best = my_photos(user, ev, threshold)
        seqs = dict(Image.objects.filter(pk__in=best).values_list("pk", "seq"))
        for image_id, score in sorted(best.items(), key=lambda kv: -kv[1]):
            self.stdout.write(f"  {seqs[image_id]:05d}  {score:.3f}")
        self.stdout.write(f"{len(best)} matching photo(s)")

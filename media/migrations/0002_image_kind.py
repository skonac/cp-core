# is_selfie (bool) becomes kind: photo | selfie | cover. Existing rows: selfie if is_selfie, else photo.

from django.db import migrations, models
from django.db.models import Q

UNFINISHED = ["pending", "processing"]


def fill_kind(apps, schema_editor):
    Image = apps.get_model("media", "Image")
    Image.objects.filter(is_selfie=True).update(kind="selfie")


class Migration(migrations.Migration):

    dependencies = [
        ("events", "0003_event_city_venue_types"),
        ("media", "0001_initial"),
    ]

    operations = [
        migrations.RemoveConstraint(model_name="image", name="image_photo_or_selfie"),
        migrations.RemoveConstraint(model_name="image", name="image_no_duplicate_per_event"),
        migrations.RemoveConstraint(model_name="image", name="selfie_one_per_slot"),
        migrations.RemoveIndex(model_name="image", name="selfie_queue"),
        migrations.AddField(
            model_name="image", name="kind",
            field=models.CharField(choices=[("photo", "Photo"), ("selfie", "Selfie"), ("cover", "Cover")],
                                   default="photo", max_length=8)),
        migrations.RunPython(fill_kind, migrations.RunPython.noop),
        migrations.RemoveField(model_name="image", name="is_selfie"),
        migrations.AddIndex(
            model_name="image",
            index=models.Index(condition=Q(kind="selfie", status__in=UNFINISHED), fields=["created_at"], name="selfie_queue")),
        migrations.AddConstraint(
            model_name="image",
            constraint=models.CheckConstraint(
                condition=Q(kind="photo", event__isnull=False, seq__isnull=False)
                | Q(kind="selfie", event__isnull=True, seq__isnull=True)
                | Q(kind="cover", event__isnull=False, seq__isnull=True),
                name="image_kind_shape")),
        migrations.AddConstraint(
            model_name="image",
            constraint=models.UniqueConstraint(condition=Q(kind="photo", deleted_at__isnull=True),
                                               fields=("event", "sha256"), name="image_no_duplicate_per_event")),
        migrations.AddConstraint(
            model_name="image",
            constraint=models.UniqueConstraint(condition=Q(kind="selfie", deleted_at__isnull=True),
                                               fields=("owner", "slot"), name="selfie_one_per_slot")),
    ]

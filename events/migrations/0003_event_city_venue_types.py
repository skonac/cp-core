# location_label becomes city (data kept), venue added, two more event types.

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("events", "0002_initial"),
    ]

    operations = [
        migrations.RenameField(model_name="event", old_name="location_label", new_name="city"),
        migrations.AddField(model_name="event", name="venue", field=models.CharField(blank=True, max_length=120)),
        migrations.AlterField(
            model_name="event", name="type",
            field=models.CharField(choices=[("wedding", "Wedding"), ("sports", "Sports"), ("graduation", "Graduation"),
                                            ("corporate", "Corporate"), ("other", "Other")],
                                   default="wedding", max_length=16)),
    ]

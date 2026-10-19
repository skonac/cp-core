# Product texts in Turkish and English. The existing texts become the Turkish ones; the English
# name starts as a copy of the Turkish name (fill in the rest in the admin).

from django.db import migrations, models
from django.db.models import F


def copy_names(apps, schema_editor):
    apps.get_model("shop", "Product").objects.update(name_en=F("name_tr"))
    apps.get_model("shop", "Purchase").objects.update(product_name_en=F("product_name_tr"))


class Migration(migrations.Migration):

    dependencies = [
        ("shop", "0002_copy_from_credits"),
    ]

    operations = [
        migrations.RenameField("product", "name", "name_tr"),
        migrations.RenameField("product", "title", "title_tr"),
        migrations.RenameField("product", "description", "description_tr"),
        migrations.AddField("product", "name_en", models.CharField(default="", max_length=40), preserve_default=False),
        migrations.AddField("product", "title_en", models.CharField(blank=True, max_length=80)),
        migrations.AddField("product", "description_en", models.CharField(blank=True, max_length=200)),
        migrations.RenameField("purchase", "product_name", "product_name_tr"),
        migrations.AddField("purchase", "product_name_en", models.CharField(default="", max_length=40),
                            preserve_default=False),
        migrations.RunPython(copy_names, migrations.RunPython.noop),
    ]

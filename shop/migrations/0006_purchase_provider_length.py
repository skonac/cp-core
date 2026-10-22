# "iyzico-production" (17 chars) did not fit in max_length=16.

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("shop", "0005_purchase_documents"),
    ]

    operations = [
        migrations.AlterField(
            model_name="purchase",
            name="provider",
            field=models.CharField(max_length=32),
        ),
    ]

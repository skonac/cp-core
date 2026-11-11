# Billing profiles for non-Turkish buyers: country as an ISO code, longer identity/tax numbers,
# district optional (design: Seko's API review, Nov 2026).

from django.db import migrations, models


def country_names_to_codes(apps, schema_editor):
    BillingProfile = apps.get_model("shop", "BillingProfile")
    BillingProfile.objects.filter(country__in=["Türkiye", "Turkey", "Turkiye", ""]).update(country="TR")


class Migration(migrations.Migration):

    dependencies = [
        ("shop", "0001_initial"),
    ]

    operations = [
        migrations.RunPython(country_names_to_codes, migrations.RunPython.noop),
        migrations.AlterField(
            model_name="billingprofile",
            name="country",
            field=models.CharField(default="TR", max_length=2),
        ),
        migrations.AlterField(
            model_name="billingprofile",
            name="identity_number",
            field=models.CharField(blank=True, max_length=20),
        ),
        migrations.AlterField(
            model_name="billingprofile",
            name="tax_number",
            field=models.CharField(blank=True, max_length=20),
        ),
        migrations.AlterField(
            model_name="billingprofile",
            name="district",
            field=models.CharField(blank=True, max_length=80),
        ),
    ]

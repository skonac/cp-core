from django.apps import AppConfig
from django.core import checks


class NotifyConfig(AppConfig):
    name = "notify"

    def ready(self):
        checks.register(check_delivery)


def check_delivery(app_configs=None, **kwargs):
    from django.conf import settings
    if settings.SMS_DELIVERY not in ("log", "live"):
        return [checks.Error(f"CP_SMS_DELIVERY={settings.SMS_DELIVERY!r}: log or live", id="cp.E010")]
    return []

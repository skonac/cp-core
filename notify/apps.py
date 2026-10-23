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
    missing = [name for name in ("API_KEY", "API_HASH", "SENDER") if not getattr(settings, f"ILETIMERKEZI_{name}")]
    if settings.SMS_DELIVERY == "live" and missing:
        return [checks.Error("CP_SMS_DELIVERY=live needs " + ", ".join(f"CP_ILETIMERKEZI_{m}" for m in missing),
                             id="cp.E011")]
    if len(settings.ILETIMERKEZI_SENDER) > 11:
        return [checks.Error(f"CP_ILETIMERKEZI_SENDER={settings.ILETIMERKEZI_SENDER!r}: at most 11 characters "
                             "(the approved sender name, exactly as in the panel)", id="cp.E012")]
    return []

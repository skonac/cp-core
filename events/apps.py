from django.apps import AppConfig
from django.core import checks


class EventsConfig(AppConfig):
    name = "events"

    def ready(self):
        checks.register(check_code_length)


def check_code_length(app_configs=None, **kwargs):
    """CP_EVENT_CODE_LENGTH must fit the code column (8) and not make codes guessable (< 5)."""
    from django.conf import settings
    if not 5 <= settings.EVENT_CODE_LENGTH <= 8:
        return [checks.Error(f"CP_EVENT_CODE_LENGTH={settings.EVENT_CODE_LENGTH}: 5 to 8", id="cp.E030")]
    return []

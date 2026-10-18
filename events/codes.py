"""Event codes: random, 5 characters from an alphabet without look-alikes (design/core.md).
Stored lowercase, shown uppercase, compared case-insensitively."""
import secrets

from django.conf import settings


def new_event_code() -> str:
    return "".join(secrets.choice(settings.EVENT_CODE_ALPHABET) for _ in range(settings.EVENT_CODE_LENGTH))


def normalize_code(text: str) -> str:
    """What the user typed or scanned -> the stored form."""
    return text.strip().lower()

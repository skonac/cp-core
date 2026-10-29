"""Event types: the one list (order = display order) with the labels cp-web shows.
GET /api/public/event-types returns it; the model's choices and the API's accepted values come from
here, so adding a type is one line + a migration (and a sample photo pair in samples/ if wanted)."""
from typing import Literal

EVENT_TYPES = [      # value, Turkish label, English label
    ("wedding", "Düğün", "Wedding"),
    ("sports", "Spor", "Sports"),
    ("graduation", "Mezuniyet", "Graduation"),
    ("corporate", "Kurumsal", "Corporate"),
    ("holiday", "Tatil", "Holiday"),
    ("vacation", "Seyahat", "Vacation"),
    ("other", "Diğer", "Other"),
]

EventType = Literal[tuple(value for value, _, _ in EVENT_TYPES)]
CHOICES = [(value, en) for value, _, en in EVENT_TYPES]


def event_types_json() -> list[dict]:
    return [{"value": value, "label": {"tr": tr, "en": en}} for value, tr, en in EVENT_TYPES]

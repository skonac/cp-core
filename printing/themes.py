"""Print themes: one folder per theme under PRINT_THEMES_DIR (print_themes/README.md, design/core.md
"Printed materials"). Read from disk on every call: few small files, and a new theme shows up without a
restart."""
import json
from dataclasses import dataclass
from pathlib import Path

from django.conf import settings

# format id -> (width mm, height mm, names). Sizes are fixed per format; themes only say which they offer.
FORMATS = {
    "poster": (420, 594, {"tr": "Afiş", "en": "Poster"}),
    "table": (74, 210, {"tr": "Masa kartı", "en": "Table card"}),
}
LANGS = ("tr", "en")


@dataclass
class Theme:
    id: str
    path: Path
    name: dict
    order: int
    formats: list
    event_types: list
    texts: dict
    fallback_image: str
    qr_dark: str

    def text(self, lang: str) -> dict:
        return self.texts.get(lang) or self.texts.get("tr") or {}


def root() -> Path:
    return Path(settings.PRINT_THEMES_DIR)


def _load(folder: Path) -> Theme:
    meta = json.loads((folder / "theme.json").read_text())
    formats = [f for f in meta.get("formats", []) if f in FORMATS and (folder / f"{f}.html").is_file()]
    return Theme(id=folder.name, path=folder, name=meta.get("name", {}), order=int(meta.get("order", 100)),
                 formats=formats, event_types=meta.get("event_types", []), texts=meta.get("texts", {}),
                 fallback_image=meta.get("fallback_image", ""), qr_dark=meta.get("qr_dark", "#000000"))


def all_themes() -> list[Theme]:
    """Every theme folder (not starting with _ or .) with a theme.json, in display order."""
    found = [_load(d) for d in sorted(root().iterdir())
             if d.is_dir() and not d.name.startswith(("_", ".")) and (d / "theme.json").is_file()]
    return sorted(found, key=lambda t: (t.order, t.id))


def get(theme_id: str) -> Theme | None:
    return next((t for t in all_themes() if t.id == theme_id), None)

"""cp-core project package. Version: major.minor by hand (as in cp-faceworker) + the build
stamp that deploy/deploy.sh writes: "0.1.37 (c3d9e2a)". A dev checkout shows "0.1-dev"."""
from pathlib import Path

__major_minor__ = "0.1"
try:
    _count, _commit = (Path(__file__).resolve().parent.parent / "BUILD").read_text().split(maxsplit=1)
    __version__ = f"{__major_minor__}.{_count} ({_commit.strip()})"
except (OSError, ValueError):
    __version__ = f"{__major_minor__}-dev"

"""Stream a ZIP of files without building it in memory or on disk.

    stream_zip([(name_in_zip, path), ...]) -> iterator of bytes

Entries are stored, not compressed (JPEGs don't shrink). zipfile writes to any object with
write(); here that object is a small buffer emptied after every chunk, so memory stays at about
one read block however big the ZIP gets. Used by the ZIP download (events/api.py).
"""
import zipfile
from pathlib import Path

CHUNK = 256 * 1024


class _Sink:
    """Write-only, non-seekable target for zipfile: collects bytes until the generator takes them."""

    def __init__(self):
        self.parts: list[bytes] = []
        self.offset = 0

    def write(self, data) -> int:
        self.parts.append(bytes(data))
        self.offset += len(data)
        return len(data)

    def tell(self) -> int:
        return self.offset

    def flush(self):
        pass

    def take(self) -> bytes:
        data, self.parts = b"".join(self.parts), []
        return data


def stream_zip(entries: list[tuple[str, Path]]):
    sink = _Sink()
    with zipfile.ZipFile(sink, mode="w", compression=zipfile.ZIP_STORED, allowZip64=True) as zf:
        for name, path in entries:
            info = zipfile.ZipInfo.from_file(path, arcname=name)
            info.compress_type = zipfile.ZIP_STORED
            with open(path, "rb") as src, zf.open(info, mode="w", force_zip64=True) as dst:
                while block := src.read(CHUNK):
                    dst.write(block)
                    yield sink.take()
            yield sink.take()
    yield sink.take()   # the central directory, written on close

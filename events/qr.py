"""Event QR code: an SVG encoding an event's joinUrl, rendered on every request (no stored file,
content never changes for an event's life). docs/api.md §3.6.

Error correction M on a joinUrl (https://cleverphotos.org/?event=RK238) comes out as a 29x29-module
code (segno picks byte mode for the mixed-case URL; forcing uppercase/alphanumeric to reach 25x25
is not wanted - mixed case stays). The margin is a fixed 4 modules per side; `width` controls pixels
per module, with any pixel remainder (from the floor division) going into the margin, split evenly,
so the declared size is always exactly `width`.
"""
import segno

MARGIN_MODULES = 4
DEFAULT_WIDTH = 370
MIN_WIDTH = 111
MAX_WIDTH = 800


def clamp_width(width: int) -> int:
    return max(MIN_WIDTH, min(MAX_WIDTH, width))


def event_qr_svg(join_url: str, width: int = DEFAULT_WIDTH) -> str:
    """SVG for `join_url` at `width` px square. `width` should already be clamped."""
    qr = segno.make(join_url, error="m")
    modules = qr.symbol_size(border=0)[0]   # 29 for today's joinUrl shape
    total_modules = modules + 2 * MARGIN_MODULES
    px = max(1, width // total_modules)
    margin = (width - modules * px) / 2   # remainder from the floor division, split both sides
    rects = [f'<rect x="{margin + x * px:g}" y="{margin + y * px:g}" width="{px}" height="{px}"/>'
             for y, row in enumerate(qr.matrix) for x, dark in enumerate(row) if dark]
    return (f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{width}" '
           f'viewBox="0 0 {width} {width}" shape-rendering="crispEdges">'
           f'<rect width="{width}" height="{width}" fill="#fff"/>'
           f'<g fill="#000">{"".join(rects)}</g></svg>')

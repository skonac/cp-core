# Print themes (basılı materyal)

One folder per theme; adding a folder adds a theme (no migration, no cp-web change). design/core.md
"Printed materials". Rendered by `printing/` in cp-core: the HTML is the preview (browser), WeasyPrint
turns the same HTML into the PDF.

```
_base.html          shared page: charset, lang, @page size, fonts, screen fit-to-window, #raw mode
_fonts/             shared fonts (fonts.css)
<theme>/
  theme.json        name, order, formats, event types, texts per language, fallback image
  poster.html       {% extends "_base.html" %}: blocks style and sheet
  table.html
  images/           the theme's own images (fallback cover, ornaments)
```

Template context: `event` (name, type, date, city, venue), `date` (formatted for `lang`), `code` (RK238),
`join_url`, `qr` (inline SVG, class "qr-code", size it in CSS), `photo` (the event's uploaded cover, else the
theme's fallback image, else empty), `name_size` (s / m / l / xl by name length), `t` (the theme's texts in
`lang`), `lang`, `asset` (URL prefix of the theme folder), `fmt` (format id and size).

Rules (browser and WeasyPrint must agree): sizes in mm/pt; fonts only from `_fonts/`; `flex: none` /
`white-space: nowrap` where a flex item must not shrink; no `margin: auto` to push items in a flex column
(position them instead); inline SVG needs explicit sizes; no JavaScript in the sheet; QR dark on light with
its quiet zone, at least 15 cm on a poster and 4 cm on a table card. Test with a 60-character event name
(staff page /dj/staff/print/).

Stock photos for the event frame preview (`GET /api/organizer/events/{code}/frame/{orientation}/preview.jpg`,
media/framing.py). JPEG, long side ≥ 1600 px, ideally 3:2 (landscape) / 2:3 (portrait).

- `default_landscape.jpg`, `default_portrait.jpg`: required (the current ones are generated placeholders).
- `<type>_<orientation>.jpg`, optional per event type (`wedding`, `sports`, `graduation`, `corporate`, `other`),
  e.g. `wedding_portrait.jpg`; the most specific existing file wins.

Replacing a file here re-renders the previews on the next request (the stock photo's date is checked too).

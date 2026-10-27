# cp-core

Cleverphotos core app: Django + PostgreSQL (pgvector). Accounts and OTP sign-in, events,
image storage, the face-worker job queue, matching, credits, downloads, the JSON API for
cp-web, and simple Django pages under `/dj/` (organizer and participant fallback UI, staff data browser).

- API contract for the frontend: [`docs/api.md`](docs/api.md) (+ [`docs/openapi.json`](docs/openapi.json), live Swagger at `/api/docs`).
- Design: `design/core.md` (outside this repo for now).

## Deploy (server)

```bash
git pull && sudo deploy/deploy.sh      # -> /opt/cleverphotos/core, settings /etc/cp-core/env
cp-core-run                            # (re)start runserver in screen "core" (dev phase)
cp-core-manage <command>               # manage.py against the deployed copy
```

First install: `deploy.sh` creates `/etc/cp-core/env` from `.env.example` and stops; edit it
(`sudoedit /etc/cp-core/env`) and run it again. Needs Postgres with the `vector` extension
(created by the postgres superuser) and the media folder (`CP_MEDIA_ROOT`, `/photodisk`).

Test data: `cp-core-manage import_photos`, `import_selfie`, `show_matches` (see `--help`). Providers from the server:
`cp-core-manage iyzico_test`, `sms_test`.

Tests: `manage.py test --noinput` (needs Postgres with pgvector); every API response is checked against
its declared schema, and a test fails when an endpoint is never called.

## Layout

| Path | What |
| --- | --- |
| `config/` | settings, root URLs, the API object, WSGI, version |
| `legal/` | Legal documents, flat. KVKK: one HTML pair per version, `kvkk_tr_002.html` + `kvkk_en_002.html`, current = `CP_KVKK_VERSION`. Sales documents (ÖBF + MSS): `obf_tr_001.html`, `mss_tr_001.html`, `obf_en_001.html`, `mss_en_001.html` (bodies) + `sales_page_001.html` (the page around them), current = `CP_SALES_DOCS_VERSION`. Never edit a published file: add a new version |
| `common/` | API envelope and errors, middleware, rate limiter |
| `accounts/` | User, OTP sign-in, uploader sessions, profile and consent API, contact form |
| `events/` | events, memberships, organizer and participant pages, gallery/organizer/uploader API |
| `media/` | images (photos, selfies, covers; also the worker queue), faces, "not me" rejections, downloads, upload pipeline, matching |
| `credits/` | credit transactions (the ledger beside the balance) |
| `shop/` | products, purchases, billing profiles, payments (iyzico, or simulated), sales documents |
| `worker/` | face worker protocol and queue |
| `staff/` | staff data browser at `/dj/staff/` (read-only; edits in `/admin/`) |
| `notify/` | pushes and messages: send log, in-process sender, providers (İletiMerkezi SMS), OTP sending |
| `deploy/` | deploy script and server commands |

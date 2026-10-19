# cp-core

Cleverphotos core app: Django + PostgreSQL (pgvector). Accounts and OTP sign-in, events,
image storage, the face-worker job queue, matching, credits, downloads, the JSON API for
cp-web, and simple Django pages (organizer and participant fallback UI).

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

Test data: `cp-core-manage import_photos`, `import_selfie`, `show_matches` (see `--help`).

## Layout

| Path | What |
| --- | --- |
| `config/` | settings, root URLs, the API object, WSGI, version |
| `kvkk/` | KVKK documents, one pair per version: `kvkk_tr_001.pdf` + `kvkk_en_001.pdf`; current = `CP_KVKK_VERSION`. Never edit a published file: add a new version |
| `common/` | API envelope and errors, middleware, rate limiter |
| `accounts/` | User, OTP sign-in, profile API |
| `events/` | events, memberships, organizer and participant pages, event API |
| `media/` | images (photos + selfies, also the worker queue), faces, downloads, upload pipeline, matching |
| `credits/` | credit transactions |
| `worker/` | face worker protocol and queue |
| `deploy/` | deploy script and server commands |

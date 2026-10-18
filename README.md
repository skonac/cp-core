# cp-core

Cleverphotos core app: Django + PostgreSQL (pgvector). Accounts, events, image storage,
the face-worker job queue, matching, credits, downloads, and the JSON API for cp-web.

Design: `design/core.md` (outside this repo for now). API notes for the frontend: `docs/api.md`.

## Dev setup (server)

```bash
uv venv --python 3.12 && uv pip install -r requirements.txt
cp .env.example .env            # then set CP_SECRET_KEY
.venv/bin/python manage.py check
```

Database: `cpcore`, owned by your Linux user (peer auth over the unix socket), with the
`vector` extension created by the postgres superuser.

## Layout

| Path | What |
| --- | --- |
| `config/` | settings, root URLs, the API object, WSGI |
| `common/` | API envelope and errors, request ids |
| `accounts/` | User, OTP sign-in |
| `events/` | events, memberships |
| `media/` | images (photos + selfies, also the worker queue), faces, downloads |
| `credits/` | credit transactions |
| `worker/` | face worker API and monitoring |

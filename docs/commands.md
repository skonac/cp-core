# cp-core command line reference

Everything you can run on the server (cleverphotos.org, as `ogesli`) to deploy, run, inspect and test cp-core.
New management command or script? Add it here.

> **The server is live** (`CP_DEBUG=0`, iyzico production, email and Telegram live). Commands marked ⚠ send real
> mail/messages, take real money or change real accounts. See "Careful in production" at the end.

## 1. The wrapper scripts

Installed into `/usr/local/bin` by `deploy.sh`, so they work from any directory.

| Command | What it does |
| --- | --- |
| `cd ~/cleverphotos/cp-core && git pull && sudo deploy/deploy.sh` | **Deploy** the checked-out commit to `/opt/cleverphotos/core`: copy code, Python 3.12 + venv, install requirements, `check`, `migrate`, `collectstatic`, install/refresh the systemd unit, restart. Refuses uncommitted changes. |
| `sudo deploy/deploy.sh --force` | Deploy with uncommitted changes (the build shows `-dirty`). |
| `cp-core-run` | Restart the service (gunicorn) and wait until `/api/health` answers (about 4 s). Asks for sudo by itself. |
| `cp-core-manage <command> [args]` | Run a Django management command against the deployed copy with the server's settings (`/etc/cp-core/env`), as the service user `cpcore`. All of section 3 and 4 goes through this. |
| `cp-core-runserver` | Fallback: Django's development server in the screen session `core` instead of gunicorn. Stop the service first: `sudo systemctl stop cp-core`. Watch: `sudo -u cpcore screen -r core`. Back to normal: `sudo -u cpcore screen -S core -X quit; cp-core-run`. |

`deploy.sh` settings (environment variables, rarely needed): `CP_SERVICE_USER` (default `cpcore`), `DEST` (default
`/opt/cleverphotos/core`), `UV` (path to `uv` if not found).

## 2. Service, logs, database, files

| Command | What it does |
| --- | --- |
| `systemctl status cp-core` | Is gunicorn running, since when, memory. |
| `sudo systemctl stop cp-core` / `start` / `restart` | Plain service control (`cp-core-run` = restart + health check). |
| `sudo journalctl -u cp-core -f` | Live log: every request (access log) and every error with its traceback. `-n 100` for the last 100 lines, `--since "10 min ago"`. |
| `sudo journalctl -u cp-core \| grep req_3dc9...` | Find what happened to one request (the `requestId` a user or an alert quotes). |
| `sudo journalctl -u faceworker -f` | The face worker's log. |
| `curl -s http://127.0.0.1:8000/api/health` | Version, build, database, pgvector. |
| `curl -s http://127.0.0.1:8000/api/operation-mode` | The operation mode (also on the staff index). |
| `sudo -u cpcore psql cpcore` | SQL as the app (peer auth: role = Linux user). Or `cp-core-manage dbshell`. |
| `ssh -L 8081:127.0.0.1:8081 ogesli@cleverphotos.org` (from the Mac) | Then http://localhost:8081/photodisk/: browse the photo volume (nginx, server-local only). |

Settings: `sudoedit /etc/cp-core/env`, then `cp-core-run` (settings are read at start). Never overwritten by deploys;
`.env.example` in the repo lists every setting with comments.

## 3. Our management commands (`cp-core-manage …`)

### Operation mode

| Command | What it does |
| --- | --- |
| `operation_mode` | The current mode and the last 10 changes. |
| `operation_mode restricted "reason"` ⚠ | Payments off for everyone except staff. |
| `operation_mode lockdown "reason"` ⚠ | Site closed for everyone except staff. |
| `operation_mode normal` | Back to normal (reason ignored). |

The reason is required for restricted and lockdown; it goes to the history and the `system.mode` alert. The running
server follows within 5 s. Same switch as the panel on `/dj/staff/`.

### Admin alerts (Telegram / email)

| Command | What it does |
| --- | --- |
| `alert_test --routes` | Every alert topic and where it goes now (`CP_NOTIFY_*`). |
| `alert_test <topic> ["text"]` ⚠ | Send a test alert to that topic's real targets, e.g. `alert_test shop.fail`. |
| `telegram_test [critical\|info] ["text"]` ⚠ | Post straight to the critical (default) or info Telegram channel, no topic involved. |

### Email

| Command | What it does |
| --- | --- |
| `email_test --accounts` | Zoho Mail: the account id and from-addresses the token sees (no mail sent). |
| `email_test <address>` ⚠ | One test mail through the normal router (`CP_EMAIL_DELIVERY` decides: `log` or real). |
| `email_test <address> --via cpaas\|zohomail` ⚠ | One mail through that provider, whatever the setting. `--via log`: only logged. |
| `email_test <address> --subject "…" --text "…"` | Own subject/text; a text starting with `<` is sent as HTML. |

### SMS (İletiMerkezi; parked, not used for sign-in)

| Command | What it does |
| --- | --- |
| `sms_test --senders` | Approved sender names. |
| `sms_test --balance` | Account balance. |
| `sms_test <+90…>` | One SMS through the router (`CP_SMS_DELIVERY`, default `log`: nothing leaves the server). |
| `sms_test <+90…> --via iletimerkezi` ⚠ | One real SMS (costs credit). `--text "…"` for own text. |

### Payments (iyzico)

| Command | What it does |
| --- | --- |
| `iyzico_test` | Ping iyzico with the configured provider's URL and keys (no purchase). |
| `iyzico_test --buy <+90…> --product <id> [--site https://cleverphotos.org]` ⚠⚠ | Start a purchase for that account and print the payment page URL. **With `iyzico-production` this is a real card payment.** The account needs the organizer role and a complete billing profile; product ids: admin → Shop → Products. |

### Photos, selfies, matching (testing)

| Command | What it does |
| --- | --- |
| `import_photos --event <CODE> <files or folders…>` ⚠ | Upload photos into an event through the same accept step as the web (spends the owner's credits). The command runs as `cpcore`, so the files must be readable by that user: if it reports a permission error, copy them somewhere neutral first (e.g. `cp -r ~/cleverphotos/testdata/event/family /tmp/` and import from `/tmp/family`). |
| `import_photos --new "Name" --owner <+90…> <files…>` ⚠ | Create an event for that owner (made organizer; account created if missing) and upload into it. |
| `import_selfie <+90…> <file> [--slot primary\|secondary] [--consent]` ⚠ | Upload a selfie for that account (created if missing). `--consent` records KVKK consent on the person's behalf: **testing only, never for a real customer**. |
| `show_matches <CODE> <+90…> [--threshold 0.3]` | Print the account's matched photos in the event, with scores (read-only). |

These three take a **phone number**; accounts without a phone (email sign-up) can't be named here yet.

## 4. Django's built-in commands worth knowing (`cp-core-manage …`)

| Command | What it does |
| --- | --- |
| `check` | Settings and our startup guards (master pass, payment provider, Turnstile, KVKK/sales files, discount). `deploy.sh` runs it; run it after editing the env. |
| `showmigrations` | Which migrations are applied. |
| `migrate` | Apply migrations (`deploy.sh` does it). |
| `createsuperuser` | New admin account: asks for a phone and a password (add the email in the admin afterwards). |
| `changepassword <+90…>` | Set an account's password (by phone). Accounts without a phone: the admin's user page. |
| `shell` | Python with the project loaded, e.g. `from accounts.models import User; User.objects.count()`. |
| `dbshell` | psql on the app's database. |
| `clearsessions` | Delete expired login sessions from the database (nothing runs this automatically; harmless any time). |
| `collectstatic --noinput` | Refresh `static_root` (`deploy.sh` does it, as root). |

**Never on the server:** `makemigrations` (migrations are written in the repo and deployed), `flush` / `reset_db`-style
commands (they wipe the database), `sendtestemail` (Django's own mailer, not ours: use `email_test`).

## 5. Careful in production

- ⚠ **Real effects:** `iyzico_test --buy` (real payment), `email_test` (real mail when delivery is live or with `--via`),
  `alert_test` / `telegram_test` (real messages to the admin group, Seko included), `sms_test --via iletimerkezi` (real SMS),
  `operation_mode` (changes the site for every user), `import_*` (creates accounts and events, spends credits).
- `import_selfie --consent` gives consent on someone's behalf: only for test accounts.
- Settings changes need `cp-core-run`; code changes need a deploy. Neither is needed for the operation mode.
- After a reboot nothing needs starting: systemd starts `cp-core` and `faceworker`.

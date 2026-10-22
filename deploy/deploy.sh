#!/usr/bin/env bash
# Install or update cp-core from this checkout into /opt/cleverphotos/core. Idempotent.
# Run it from your clone after `git pull`:
#
#   sudo deploy/deploy.sh            # deploy the checked-out commit
#   sudo deploy/deploy.sh --force    # also allow uncommitted changes (commit ends in -dirty)
#
# Dev phase: no systemd service. Django runs as `runserver` in the screen session "core",
# as the user who ran sudo (your user owns the database and /photodisk). The script
# restarts that session at the end. Settings: /etc/cp-core/env, created from .env.example
# only when missing (then the script stops so you can edit it), never overwritten.
set -euo pipefail

DEST=${DEST:-/opt/cleverphotos/core}
CONF_DIR=/etc/cp-core
PY=3.12

[ "$(id -u)" = 0 ] || { echo "run with sudo" >&2; exit 1; }
RUN_USER=${SUDO_USER:?"run with sudo from your own user, not as root"}
for cmd in git rsync runuser screen; do
  command -v "$cmd" >/dev/null || { echo "missing $cmd (apt install $cmd)" >&2; exit 1; }
done
SRC=$(cd "$(dirname "$0")/.." && pwd)
FORCE=0; [ "${1:-}" = "--force" ] && FORCE=1

say() { printf '\n== %s\n' "$*"; }
git_() { git -c safe.directory="$SRC" -C "$SRC" "$@"; }
as_user() { runuser -u "$RUN_USER" -- "$@"; }

UV=${UV:-$(command -v uv || true)}
[ -n "$UV" ] || UV="$(getent passwd "$RUN_USER" | cut -d: -f6)/.local/bin/uv"
[ -x "$UV" ] || { echo "uv not found; run with UV=/path/to/uv" >&2; exit 1; }

say "source $SRC"
COUNT=$(git_ rev-list --count HEAD)
COMMIT=$(git_ rev-parse --short HEAD)
if ! git_ diff --quiet HEAD --; then
  if [ "$FORCE" = 0 ]; then
    echo "uncommitted changes; commit them or use --force:" >&2
    git_ status --short >&2
    exit 1
  fi
  COMMIT="$COMMIT-dirty"
fi
echo "build $COUNT ($COMMIT)"

say "code -> $DEST"
mkdir -p "$DEST"
rsync -a --delete --exclude .git --exclude .venv --exclude python --exclude .env \
      --exclude __pycache__ --exclude media_root --exclude static_root --exclude BUILD \
      "$SRC/" "$DEST/"
echo "$COUNT $COMMIT" > "$DEST/BUILD"
chown -R root:root "$DEST"
echo "ok"

say "python $PY and venv"
export UV_PYTHON_INSTALL_DIR="$DEST/python"
"$UV" -q python install "$PY"
[ -x "$DEST/.venv/bin/python" ] || "$UV" -q venv --python "$PY" "$DEST/.venv"
"$UV" -q pip install --python "$DEST/.venv/bin/python" -r "$DEST/requirements.txt"   # resolves dependencies (requirements.txt lists top-level packages only)
readlink -f "$DEST/.venv/bin/python"

say "settings $CONF_DIR/env"
install -d -m 750 -o root -g "$RUN_USER" "$CONF_DIR"
if [ -f "$CONF_DIR/env" ]; then echo "kept existing env"; else
  install -m 640 -o root -g "$RUN_USER" "$DEST/.env.example" "$CONF_DIR/env"
  sed -i "s|^CP_SECRET_KEY=.*|CP_SECRET_KEY=$(head -c 48 /dev/urandom | base64 | tr -d '/+=\n')|" "$CONF_DIR/env"
  echo "created from .env.example (new secret key)."
  echo "Check it (database user, CP_WORKER_TOKEN):  sudoedit $CONF_DIR/env"
  echo "then run this script again."
  exit 0
fi

say "commands /usr/local/bin/cp-core-manage, cp-core-run"
install -m 755 "$DEST/deploy/cp-core-manage" "$DEST/deploy/cp-core-run" /usr/local/bin/
echo "ok"

say "check + migrate (as $RUN_USER)"
as_user cp-core-manage check
as_user cp-core-manage migrate --noinput

say "collectstatic -> $DEST/static_root"
cp-core-manage collectstatic --noinput -v 0   # as root: $DEST is root-owned; runserver (DEBUG) serves from static/ directly
echo "ok"

say "restart runserver in screen 'core' (as $RUN_USER)"
as_user /usr/local/bin/cp-core-run

#!/usr/bin/env bash
# Bootstrap / update the annotation app on the Informatics VM
# (breezy.inf.ed.ac.uk). Idempotent: run once to deploy, re-run any time to
# pull the latest main and restart.
#
#   bash <(curl -sL https://raw.githubusercontent.com/IYURA2006/llm-playschool/main/vm/setup_vm.sh)
#
# Deliberately NOT here, versus the pre-Postgres version of this script:
#   * no DATA_DIR — the database is managed PostgreSQL, not a SQLite file, so
#     the app holds no durable state on disk and there is nothing to bind-mount
#     or to keep across a redeploy.
#   * no Docker path — breezy runs the app under a systemd user service behind
#     Apache. A second deployment path that nobody exercises is a liability.
#   * no generated .env — this script REFUSES to invent database credentials.
#     The old version wrote a DATA_DIR-only .env, which made the app die inside
#     _require_db_config() at import while Apache went on serving a 503 with
#     nothing in the app log to explain it.
set -euo pipefail

REPO_URL=${REPO_URL:-https://github.com/IYURA2006/llm-playschool.git}
BRANCH=${BRANCH:-main}
APP_DIR=${APP_DIR:-$HOME/llm-playschool}
BACKUP_DIR=${BACKUP_DIR:-$HOME/annotation-backups}

say() { printf '>> %s\n' "$*"; }
die() { printf '!! %s\n' "$*" >&2; exit 1; }

# ── Code ─────────────────────────────────────────────────────────────────────
if [ ! -d "$APP_DIR/.git" ]; then
    say "cloning $BRANCH into $APP_DIR"
    git clone -b "$BRANCH" "$REPO_URL" "$APP_DIR"
else
    say "updating $APP_DIR to latest $BRANCH"
    git -C "$APP_DIR" fetch origin "$BRANCH"
    git -C "$APP_DIR" checkout "$BRANCH"
    git -C "$APP_DIR" pull --ff-only origin "$BRANCH"
fi
cd "$APP_DIR"

# ── .env ─────────────────────────────────────────────────────────────────────
# Stop here rather than start a process that cannot possibly work.
if [ ! -f .env ]; then
    cp .env.example .env
    chmod 600 .env
    die ".env did not exist — a template was copied to $APP_DIR/.env.
    Fill in DB_PASSWORD (and check DB_HOST), make sure GAMES_DIR=games_study,
    then re-run this script."
fi
chmod 600 .env
for var in DB_HOST DB_NAME DB_USER DB_PASSWORD; do
    grep -qE "^${var}=.+" .env || die "$var is empty in $APP_DIR/.env — the app \
cannot start without it. Fill it in and re-run."
done
grep -qE "^GAMES_DIR=games_study$" .env \
    || die "GAMES_DIR must be games_study in $APP_DIR/.env, or the study runs \
on the wrong corpus (app.py refuses to boot without it)."

# The app's port, and therefore what Apache must be proxying to. app.py
# defaults to 3000; .env may override it.
PORT=$(sed -n 's/^PORT=\([0-9]\+\).*/\1/p' .env | tail -1)
PORT=${PORT:-3000}

# ── Python ───────────────────────────────────────────────────────────────────
# gradio 6.x needs a modern interpreter; uv provides a user-local 3.13 without
# sudo if the system python3 is too old.
if python3 -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 11) else 1)' 2>/dev/null; then
    [ -d .venv ] || python3 -m venv .venv
else
    say "system python3 is older than 3.11 — using uv to get 3.13"
    UV=$(command -v uv || echo "$HOME/.local/bin/uv")
    [ -x "$UV" ] || { curl -LsSf https://astral.sh/uv/install.sh | sh; UV=$HOME/.local/bin/uv; }
    [ -d .venv ] || "$UV" venv --python 3.13 .venv
fi
say "installing dependencies"
.venv/bin/pip install --quiet --upgrade pip
.venv/bin/pip install --quiet -r requirements.txt

# ── Database reachability + schema ───────────────────────────────────────────
# Checked before the service starts, so a DB problem reports itself here rather
# than as a restart loop in journalctl.
say "checking database connectivity"
.venv/bin/python - <<'PY' || die "cannot reach the database with the credentials in .env — fix those first."
import sys
from dotenv import load_dotenv; load_dotenv()
import os, psycopg2
psycopg2.connect(host=os.environ["DB_HOST"], port=os.environ.get("DB_PORT", "5432"),
                 dbname=os.environ["DB_NAME"], user=os.environ["DB_USER"],
                 password=os.environ["DB_PASSWORD"],
                 sslmode=os.environ.get("DB_SSLMODE", "require"),
                 gssencmode=os.environ.get("DB_GSSENCMODE", "disable")).close()
print(">> database reachable")
PY

# ── Apache: verify, do not assume ────────────────────────────────────────────
# The vhost is managed by computing support and is the authority on which port
# the app must listen on. A mismatch here is invisible from the app side: the
# process looks healthy and Apache serves 503.
APACHE_PORTS=$(grep -rhoE 'ProxyPass\s+.*127\.0\.0\.1:([0-9]+)' \
    /etc/apache2/sites-enabled/ /etc/httpd/conf.d/ 2>/dev/null \
    | grep -oE '[0-9]+$' | sort -u || true)
if [ -z "$APACHE_PORTS" ]; then
    say "NOTE: could not read the Apache vhost (permissions?). Confirm with"
    say "      computing support that it proxies to 127.0.0.1:$PORT."
elif ! printf '%s\n' "$APACHE_PORTS" | grep -qx "$PORT"; then
    say "WARNING: Apache proxies to port(s) [$(echo $APACHE_PORTS | tr '\n' ' ')]"
    say "         but this app will listen on $PORT. They must match, or every"
    say "         request returns 503. Either set PORT=<apache port> in .env or"
    say "         have the vhost repointed at $PORT."
else
    say "Apache proxies to 127.0.0.1:$PORT — matches the app"
fi

# ── systemd user service ─────────────────────────────────────────────────────
if command -v systemctl >/dev/null 2>&1 && systemctl --user show-environment >/dev/null 2>&1; then
    mkdir -p "$HOME/.config/systemd/user"
    sed "s|__APP_DIR__|$APP_DIR|g" vm/annotation.service \
        > "$HOME/.config/systemd/user/annotation.service"
    systemctl --user daemon-reload
    systemctl --user enable annotation.service >/dev/null
    systemctl --user restart annotation.service
    say "systemd user service (re)started"
    if ! loginctl show-user "$USER" 2>/dev/null | grep -q 'Linger=yes'; then
        say "IMPORTANT: linger is OFF — the app will die when you log out."
        say "           Run 'loginctl enable-linger $USER', or ask computing support to."
    fi
else
    say "systemd user services unavailable — run it under tmux instead:"
    say "  tmux new -s annotation"
    say "  cd $APP_DIR && .venv/bin/python app.py"
fi

# ── Nightly pg_dump ──────────────────────────────────────────────────────────
mkdir -p "$BACKUP_DIR"
chmod +x vm/backup_db.sh
CRON_LINE="17 3 * * * $APP_DIR/vm/backup_db.sh >> $BACKUP_DIR/backup.log 2>&1"
{ crontab -l 2>/dev/null | grep -vF 'vm/backup_db.sh' || true; echo "$CRON_LINE"; } | crontab -
say "nightly pg_dump installed (crontab -l to inspect)"

# ── Smoke check ──────────────────────────────────────────────────────────────
for _ in $(seq 1 20); do
    curl -sfI "http://127.0.0.1:$PORT/" >/dev/null 2>&1 && break
    sleep 1
done
if curl -sfI "http://127.0.0.1:$PORT/" >/dev/null 2>&1; then
    say "OK: app answering on http://127.0.0.1:$PORT — try the public URL now."
else
    say "app is NOT answering on 127.0.0.1:$PORT. Check:"
    say "  systemctl --user status annotation.service"
    say "  journalctl --user -u annotation.service -n 50 --no-pager"
fi

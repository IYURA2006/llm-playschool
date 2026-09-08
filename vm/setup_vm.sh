#!/usr/bin/env bash
# Bootstrap / update the annotation app on the Informatics VM
# (breezy.inf.ed.ac.uk). Idempotent: run once to deploy, re-run any time to
# pull the latest main and restart.
#
#   bash <(curl -sL https://raw.githubusercontent.com/IYURA2006/llm-playschool/main/vm/setup_vm.sh)
#
# Left out on purpose:
#   * no DATA_DIR. The database is managed PostgreSQL, so the app keeps no
#     durable state on disk and there is nothing to preserve across a redeploy.
#   * no Docker path. breezy runs a systemd user service behind Apache, and a
#     second deployment path nobody uses is a liability.
#   * no generated .env. This script will not invent database credentials: the
#     app would then die at import while Apache served a 503 with nothing in
#     the log to explain it.
set -euo pipefail

REPO_URL=${REPO_URL:-https://github.com/IYURA2006/llm-playschool.git}
REPO_URL_RAW=${REPO_URL_RAW:-https://raw.githubusercontent.com/IYURA2006/llm-playschool/main}
BRANCH=${BRANCH:-main}
APP_DIR=${APP_DIR:-$HOME/llm-playschool}
# Everything the app writes goes next to the app, not under $HOME. On breezy
# $HOME is AFS, and neither cron nor the systemd user manager holds an AFS
# token, so anything under $HOME is unwritable to exactly those two.
LOCAL_STATE=${LOCAL_STATE:-$(dirname "$APP_DIR")}
BACKUP_DIR=${BACKUP_DIR:-$LOCAL_STATE/annotation-backups}
SVC_HOME=${SVC_HOME:-$LOCAL_STATE/svc-home}
LOG_FILE=${LOG_FILE:-$LOCAL_STATE/app.log}
HOME_FS=$(df -PT "$HOME" 2>/dev/null | tail -1 | awk '{print $2}')

say() { printf '>> %s\n' "$*"; }
die() { printf '!! %s\n' "$*" >&2; exit 1; }

# Filesystem type of the nearest ancestor of $1 that exists, so this can be
# asked about a directory the script has not created yet.
fstype_of() {
    local p=$1
    while [ ! -e "$p" ] && [ "$p" != "/" ]; do p=$(dirname "$p"); done
    df -PT "$p" 2>/dev/null | tail -1 | awk '{print $2}'
}

# ── Refuse to deploy onto AFS ────────────────────────────────────────────────
# The service writes its log, its HOME and its backups under LOCAL_STATE, and
# neither the systemd user manager nor cron holds an AFS token. Everything
# below would still appear to succeed: the code checks out, the venv builds,
# the database answers, the transient unit registers — and then the unit dies
# on the spot because systemd cannot open StandardOutput=append:$LOG_FILE.
#
# It fails here, before the clone, for two reasons. Stopping later would leave
# a second checkout in AFS whose .env is the template, which reads as lost
# credentials rather than the wrong directory. And the restart happens after
# the old service is stopped, so a late failure takes a working site down —
# which is exactly what happened on 2026-09-08.
if [ "$(fstype_of "$LOCAL_STATE")" = "afs" ]; then
    SUGGEST=/disk/data/$USER/llm-playschool
    [ -d "$(dirname "$SUGGEST")" ] || SUGGEST="<a path on local disk>/llm-playschool"
    die "LOCAL_STATE is on AFS ($LOCAL_STATE).
    The app's log, HOME and backups go there, and daemons hold no AFS token,
    so the service would register and then die without writing a log.
    Re-run with the path pinned to local disk:

        APP_DIR=$SUGGEST bash <(curl -sL $REPO_URL_RAW/vm/setup_vm.sh)

    Override LOCAL_STATE explicitly if the app really must live on AFS."
fi

# ── Code ─────────────────────────────────────────────────────────────────────
if [ ! -d "$APP_DIR/.git" ]; then
    say "cloning $BRANCH into $APP_DIR"
    git clone -b "$BRANCH" "$REPO_URL" "$APP_DIR"
else
    # The remote is otherwise set only at clone time, so an existing checkout
    # keeps pulling from the repo it was first cloned from and changing
    # REPO_URL would appear to work while deploying the old one.
    CURRENT_URL=$(git -C "$APP_DIR" remote get-url origin 2>/dev/null || echo "")
    if [ -n "$CURRENT_URL" ] && [ "$CURRENT_URL" != "$REPO_URL" ]; then
        say "repointing origin"
        say "    from $CURRENT_URL"
        say "      to $REPO_URL"
        git -C "$APP_DIR" remote set-url origin "$REPO_URL"
    fi
    say "updating $APP_DIR to latest $BRANCH"
    git -C "$APP_DIR" fetch origin "$BRANCH"
    git -C "$APP_DIR" checkout "$BRANCH"
    # --ff-only refuses instead of merging, so a force-push upstream stops here
    # with a clear error. Recover with: git -C "$APP_DIR" reset --hard origin/main
    git -C "$APP_DIR" pull --ff-only origin "$BRANCH"
fi
cd "$APP_DIR"

# Stop here rather than start a process that cannot work.
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

# The port Apache must proxy to. app.py defaults to 3000; .env can override it.
PORT=$(sed -n 's/^PORT=\([0-9]\+\).*/\1/p' .env | tail -1)
PORT=${PORT:-3000}

# gradio 6 needs a modern interpreter. uv installs a user-local 3.13 without
# sudo when the system python3 is too old.
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
# numpy's wheels are built for x86-64-v2, which this VM's CPU does not expose.
# The failure shows up deep inside gradio, so check it here where the message
# can be clear.
if ! .venv/bin/python -c "import numpy" >/dev/null 2>&1; then
    say "the numpy wheel does not run on this CPU — installing a baseline build"
    .venv/bin/pip install --quiet "numpy<2" \
        || die "could not install a numpy build for this CPU."
fi

# Checked before the service starts, so a database problem reports itself here
# rather than as a restart loop in journalctl.
say "checking database connectivity"
.venv/bin/python - <<'PY' || die "cannot reach the database with the credentials in .env — fix those first."
import sys
# Explicit path, not bare load_dotenv(): this block reaches python on stdin,
# so find_dotenv() has no file to search from and fails.
from dotenv import load_dotenv; load_dotenv(".env")
import os, psycopg2
psycopg2.connect(host=os.environ["DB_HOST"], port=os.environ.get("DB_PORT", "5432"),
                 dbname=os.environ["DB_NAME"], user=os.environ["DB_USER"],
                 password=os.environ["DB_PASSWORD"],
                 sslmode=os.environ.get("DB_SSLMODE", "require"),
                 gssencmode=os.environ.get("DB_GSSENCMODE", "disable")).close()
print(">> database reachable")
PY

# Computing support manages the vhost, and it decides which port the app must
# use. A mismatch is invisible from the app side: the process looks healthy
# while Apache serves 503.
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

# HOME is overridden for the service. libpq looks for a client certificate in
# $HOME/.postgresql/ and treats AFS's "permission denied" as fatal rather than
# as "no certificate". gradio also writes caches under $HOME.
mkdir -p "$SVC_HOME"
SVC_RUN="systemd-run --user --unit=annotation --working-directory=$APP_DIR"
SVC_RUN="$SVC_RUN --setenv=HOME=$SVC_HOME --setenv=PYTHONUNBUFFERED=1"
SVC_RUN="$SVC_RUN --property=Restart=always --property=RestartSec=5"
SVC_RUN="$SVC_RUN --property=StandardOutput=append:$LOG_FILE"
SVC_RUN="$SVC_RUN --property=StandardError=append:$LOG_FILE"
SVC_RUN="$SVC_RUN $APP_DIR/.venv/bin/python app.py"

if command -v systemctl >/dev/null 2>&1 && systemctl --user show-environment >/dev/null 2>&1; then
    if [ "$HOME_FS" = "afs" ]; then
        # systemd reads user units only from ~/.config/systemd/user, and the
        # user manager has no AFS token. The file is written and readable by
        # you, but systemd reports "Unit file does not exist". Register a
        # transient unit instead: those live in /run/user/UID, on local disk.
        say "AFS home detected — registering a transient unit (systemd cannot read units on AFS)"
        systemctl --user stop annotation >/dev/null 2>&1 || true
        systemctl --user reset-failed annotation >/dev/null 2>&1 || true
        eval "$SVC_RUN" >/dev/null || die "could not start the transient service unit."
        # Transient units do not survive a reboot, so cron re-creates it.
        # systemd-run --user needs XDG_RUNTIME_DIR to find the user manager's
        # bus, and cron runs without it. Without this line the @reboot entry
        # fails with "Failed to connect to bus", only when it is needed.
        { crontab -l 2>/dev/null | grep -vF 'unit=annotation' || true;
          echo "@reboot XDG_RUNTIME_DIR=/run/user/$(id -u) $SVC_RUN"; } | crontab -
        say "transient service started; an @reboot cron entry re-creates it after a reboot"
    else
        mkdir -p "$HOME/.config/systemd/user"
        sed -e "s|__APP_DIR__|$APP_DIR|g" -e "s|__SVC_HOME__|$SVC_HOME|g" \
            -e "s|__LOG_FILE__|$LOG_FILE|g" vm/annotation.service \
            > "$HOME/.config/systemd/user/annotation.service"
        systemctl --user daemon-reload
        systemctl --user enable annotation.service >/dev/null
        systemctl --user restart annotation.service
        say "systemd user service (re)started"
    fi
    if ! loginctl show-user "$USER" 2>/dev/null | grep -q 'Linger=yes'; then
        say "IMPORTANT: linger is OFF — the app will die when you log out."
        say "           Run 'loginctl enable-linger $USER', or ask computing support to."
    fi
else
    say "systemd user services unavailable — run it under tmux instead:"
    say "  tmux new -s annotation"
    say "  cd $APP_DIR && .venv/bin/python app.py"
fi
say "logs: tail -f $LOG_FILE"

# ── Nightly pg_dump ──────────────────────────────────────────────────────────
mkdir -p "$BACKUP_DIR"
chmod +x vm/backup_db.sh
# BACKUP_DIR must be passed, not inherited: cron starts with a bare
# environment, so backup_db.sh would fall back to its $HOME default and try
# to write the dump to AFS, which cron has no token for. The redirect below
# already points at local disk, so the failure would land in backup.log and
# nowhere else — a study running with no backups and nothing obviously wrong.
CRON_LINE="17 3 * * * APP_DIR=$APP_DIR BACKUP_DIR=$BACKUP_DIR $APP_DIR/vm/backup_db.sh >> $BACKUP_DIR/backup.log 2>&1"
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

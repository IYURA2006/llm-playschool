# Deploying the annotation app on breezy.inf.ed.ac.uk

Everything the study runs on lives on `main`. There is one deployment path:
a Python venv under a **systemd user service**, listening on `127.0.0.1:3000`,
behind the **Apache** reverse proxy that computing support manages.

The database is managed **PostgreSQL**. The app keeps no durable state on
disk, so a redeploy is just "pull, reinstall, restart" — nothing to preserve
in the working directory except `.env`.

## Access

Informatics hosts are not reachable from the open internet — connect to the
University VPN (or be on campus) first.

```bash
ssh s2634187@breezy.inf.ed.ac.uk
# if direct SSH fails on campus:
ssh -J s2634187@ssh.inf.ed.ac.uk s2634187@breezy.inf.ed.ac.uk
```

## Deploy, or update

```bash
bash <(curl -sL https://raw.githubusercontent.com/IYURA2006/llm-playschool/main/vm/setup_vm.sh)
```

Idempotent — re-run it to pull the latest `main` and restart. It clones or
updates the repo, validates `.env`, builds the venv, checks the database is
reachable, compares the app's port against the Apache vhost, installs and
restarts the systemd user service, schedules a nightly `pg_dump`, and
smoke-checks the port.

On a first run it will stop and tell you to fill in `.env`. That is deliberate:
it refuses to invent database credentials, because an app started without them
dies inside `_require_db_config()` at import while Apache goes on serving a
503 with nothing in the app's own log to explain it.

## `.env` on the VM

```ini
DB_HOST=localhost          # or the managed instance's host
DB_NAME=study
DB_USER=studyuser
DB_PASSWORD=<real password>
DB_SSLMODE=require
DB_GSSENCMODE=disable
GAMES_DIR=games_study
```

- `GAMES_DIR=games_study` is mandatory. `games/` is the 234-transcript pilot
  pool and shares zero slugs with the study's 416; `app.py` refuses to boot if
  the inventory disagrees with the batch manifest.
- **Do not set `PORT`** unless Apache is proxying somewhere other than 3000.
- **Do not set `GRADIO_SERVER_NAME`.** Gradio's default 127.0.0.1 bind is what
  keeps Apache the only public entry point; `0.0.0.0` would publish the app
  directly over plain HTTP.

## Database, once

```bash
psql "host=localhost dbname=study sslmode=require gssencmode=disable" -f postgres_schema.sql
```

`postgres_grants.sql` needs an admin role — the app's own user cannot run it.

If `annotations` already exists from an earlier deployment, confirm the later
columns actually landed. `CREATE TABLE IF NOT EXISTS` is a no-op on an existing
table, so a database created before they were added passes the table check
while silently dropping the data:

```bash
psql "..." -c '\d annotations' | grep -E 'batch_id|template_id|question_set_hash'
psql "..." -c '\d question_sets'
```

## Port

Apache terminates TLS on 443 and reverse-proxies to `127.0.0.1:3000`, which is
`app.py`'s default — so no configuration is needed for them to agree, and
`setup_vm.sh` warns if they don't.

Historical note, because it has caused a 503 once already: the retired
`vm-deploy` branch's Dockerfile and nginx template used **7860**, and an older
version of this document claimed Apache forwarded to 7860. That infrastructure
targeted nginx, which breezy does not run. Treat the live Apache vhost as the
only authority:

```bash
grep -rn ProxyPass /etc/apache2/sites-enabled/ 2>/dev/null || \
grep -rn ProxyPass /etc/httpd/conf.d/
```

If it points somewhere other than 3000, set `PORT` in `.env` to match rather
than asking for the vhost to be changed.

## If pages load but buttons hang forever

Gradio 6 streams events over SSE, and a buffering proxy breaks it. The vhost
needs:

```apache
ProxyPass        "/" "http://127.0.0.1:3000/" flushpackets=on
ProxyPassReverse "/" "http://127.0.0.1:3000/"
ProxyPreserveHost On
```

## One-time asks for computing support

- `loginctl enable-linger s2634187` — without it, systemd tears down the user
  manager at logout and takes the app with it.
- Confirm the vhost serves HTTPS and has `flushpackets=on`.

## Day to day

```bash
systemctl --user status annotation.service      # is it up?
journalctl --user -u annotation.service -f      # live logs
systemctl --user restart annotation.service     # after a config change
crontab -l                                      # backup schedule
```

Study progress and data checks, from the repo directory:

```bash
.venv/bin/python export_annotations.py --check   # batch coverage, rule violations
.venv/bin/python assignment.py                   # inventory summary + preflight
```

## Backups

`vm/backup_db.sh` runs nightly via cron and writes `study-<stamp>.sql.gz` to
`~/annotation-backups`, keeping 30 days. `pg_dump` snapshots inside a single
transaction, so it is safe to run while annotators are working — there is no
one-writer rule to respect any more, unlike the SQLite setup this replaced.

Restore into a scratch database:

```bash
gunzip -c ~/annotation-backups/study-<stamp>.sql.gz \
  | psql "host=localhost dbname=study_restore sslmode=require gssencmode=disable"
```

## Troubleshooting, in the order things actually fail

| Symptom | Cause |
|---|---|
| Apache 503, app not listening | `.env` missing/incomplete → `RuntimeError: Missing required DB config` at import |
| `Refusing to start — the study inventory does not check out` | `GAMES_DIR` not `games_study`, or the corpus doesn't match the manifest |
| Apache 503, app *is* listening | Port mismatch between the vhost and the app |
| Works until you log out | Linger not enabled |
| Pages load, controls hang | SSE buffered by the proxy — needs `flushpackets=on` |
| `pip` fails installing gradio | System Python older than 3.11; re-run `setup_vm.sh` to get `uv`'s 3.13 |

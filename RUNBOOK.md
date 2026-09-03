# Deploying the annotation app on breezy.inf.ed.ac.uk

Everything the study runs on is on `main`. There is one deployment path: a
Python venv under a **systemd user service** listening on `127.0.0.1:3000`,
behind the **Apache** proxy that computing support manages.

The database is managed **PostgreSQL**. The app saves nothing lasting to disk,
so a redeploy is just pull, reinstall, restart. The only file worth keeping in
the working directory is `.env`.

## Access

Informatics hosts are not reachable from the open internet. Connect to the
University VPN, or be on campus, first.

```bash
ssh s2634187@breezy.inf.ed.ac.uk
# if direct SSH fails on campus:
ssh -J s2634187@ssh.inf.ed.ac.uk s2634187@breezy.inf.ed.ac.uk
```

## Before recruiting

```bash
GAMES_DIR=games_study python readiness.py
```

One command, go or no-go. It exits non-zero on anything blocking, and it will
refuse while the Prolific completion code is still a placeholder or while test
rows remain in the database. Database checks report SKIPPED, never passed, when
they cannot connect, so run it **on the VM** for the full picture.

It cannot see whether the VM has actually redeployed — it checks that local
matches `origin/main`, which catches a forgotten push but not a forgotten
deploy. Run the deploy below first, then readiness, in that order.

Run the assignment suite too, against a disposable database:

```bash
TEST_DB_NAME=study_test python _test_assignment.py
```

73 checks, including the one that guarantees no participant ever rates the same
game instance twice.

## Deploy, or update

```bash
bash <(curl -sL https://raw.githubusercontent.com/IYURA2006/llm-playschool/main/vm/setup_vm.sh)
```

Safe to re-run: it pulls the latest `main` and restarts. It clones or updates
the repo, repoints `origin` if `REPO_URL` changed, checks `.env`, builds the
venv, tests the database connection, compares the app's port with the Apache
vhost, restarts the service, schedules the nightly `pg_dump` and checks the
port answers.

**The VM deploys from `IYURA2006/llm-playschool`, which is public.** That is
why the VM can clone and pull with no credentials. The group repo
(`esgi-research-group/lm-playschool-human-eval`) is private, so it cannot serve
either half of the command above: `raw.githubusercontent` returns 404, and an
anonymous clone fails with `could not read Username`. Using it would mean
putting a deploy key on the VM.

Both remotes hold the same `main`. Push to **both**, or a redeploy silently
ships without whatever only reached the other one:

```bash
git push origin main && git push eval main
```

If `git pull --ff-only` fails with *"Not possible to fast-forward"*, someone
force-pushed `main`. The working directory holds nothing worth keeping except
`.env`, which is untracked, so it is safe to take the remote's version:

```bash
git -C ~/llm-playschool fetch origin main
git -C ~/llm-playschool reset --hard origin/main
```

On a first run it stops and asks you to fill in `.env`. That is deliberate: it
will not invent database credentials. An app started without them dies at
import while Apache keeps serving a 503, with nothing in the app's log.

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

- `GAMES_DIR=games_study` is required. `games/` is the pilot pool and shares no
  slugs with the study's 416. `app.py` refuses to start if the two disagree.
- **Do not set `PORT`** unless Apache is proxying somewhere other than 3000.
- **Do not set `GRADIO_SERVER_NAME`.** Gradio's default 127.0.0.1 bind keeps
  Apache the only public entry point. `0.0.0.0` would publish the app directly
  over plain HTTP.

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

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

70 checks, including the one that guarantees no participant ever rates the same
game instance twice.

And the browser test, after any change to a screen. It drives real browsers
through whole sittings, several at once, and compares every answer clicked with
the row stored. Clicking through alone does not find what this finds: the bugs
it has caught so far only appeared with six people working at the same time.

```bash
GAMES_DIR=games_pilot DB_NAME=study_test PORT=3001 python app.py     # terminal 1
GAMES_DIR=games_pilot python _test_e2e.py --participants 6 --rounds 5 --reset   # terminal 2
```

## The pilot

A small dry run of the whole pipeline on real annotators, on transcripts the
study does not use, so nothing in the 416 is spent on it.

| | |
|---|---|
| Games | ReferenceGame, Deal or No Deal, Codenames |
| Sittings | one per game: 8, 4 and 3 transcripts |
| Annotators | 2 per sitting, so 6 sittings and 30 judgements |
| One person | may take all three sittings (the cap is 3) |

`build_pilot_set.py` wrote `games_pilot/`, `pilot_manifest.csv` and
`pilot_batches.csv`. It refuses any instance that appears in
`study_manifest.csv` under any model, so a pilot annotator who later joins the
study has seen none of its game states.

**Switching is one line in `.env`**, then a redeploy. `GAMES_DIR` selects the
corpus and everything that has to agree with it — batch file, annotators per
transcript, the per-person cap (`study_profile.py`):

```bash
sed -i 's/^GAMES_DIR=.*/GAMES_DIR=games_pilot/' /disk/data/s2634187/llm-playschool/.env
APP_DIR=/disk/data/s2634187/llm-playschool bash vm/setup_vm.sh     # prints which corpus is live
.venv/bin/python readiness.py --pilot
```

Participants use the same link as the study (see "The Prolific link" below).
For people who are not on Prolific, give each a made-up id of their own,
`https://breezy.inf.ed.ac.uk/?PROLIFIC_PID=<id>`; with no `SESSION_ID` in the
link, the same id on a later visit is how they get their next sitting.

Watch it while it runs:

```bash
.venv/bin/python export_annotations.py --check    # batch progress, who has data
tail -f /disk/data/s2634187/app.log               # any Traceback here is worth reading
```

### After the pilot

The pilot writes to the same database as the study, and `readiness.py` blocks
the study while its rows remain: they would count toward a returning
participant's cap and appear in the study's export as transcripts that are in
no manifest. One command takes them out. Run it after the pilot has closed on
Prolific, from the app directory on the VM:

```bash
.venv/bin/python pilot_closeout.py              # export + verify, deletes nothing
.venv/bin/python pilot_closeout.py --delete     # the same, then delete what was exported
```

It writes `exports_pilot/pilot_<stamp>/` — a lossless raw dump of every pilot
row plus the usual decoded CSVs — reads the files back, and only then deletes
exactly those rows, in one transaction. If anyone saved a rating in between it
deletes nothing and says so. Study rows, consent records and practice records
are never touched. Copy the folder somewhere safe; it is gitignored on purpose.

Then switch back:

```bash
sed -i 's/^GAMES_DIR=.*/GAMES_DIR=games_study/' .env
APP_DIR=/disk/data/s2634187/llm-playschool bash vm/setup_vm.sh
.venv/bin/python readiness.py
```

## Deploy, or update

```bash
APP_DIR=/disk/data/s2634187/llm-playschool \
  bash <(curl -sL https://raw.githubusercontent.com/IYURA2006/llm-playschool/main/vm/setup_vm.sh)
```

**`APP_DIR` is not optional on breezy.** Without it the script deploys into
`$HOME`, which is AFS, and puts the app's log, HOME and backups there too. The
service registers and then dies immediately, because systemd holds no AFS token
and cannot open the log — after the old service has already been stopped. On
2026-09-08 the bare command took the public site to 503 and left a second
checkout in AFS whose `.env` was the template, which reads as lost database
credentials rather than the wrong directory. The script now refuses this before
it touches anything, but pass the path and the question does not arise.

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
git -C /disk/data/s2634187/llm-playschool fetch origin main
git -C /disk/data/s2634187/llm-playschool reset --hard origin/main
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

- `GAMES_DIR=games_study` is the study and the default. `games_pilot` is the
  pilot (see above). Nothing else is accepted, and `app.py` refuses to start if
  the corpus and its batch file disagree.
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

### The Prolific link, and the column behind it

Set the study URL on Prolific to pass all three ids:

```
https://breezy.inf.ed.ac.uk/?PROLIFIC_PID={{%PROLIFIC_PID%}}&STUDY_ID={{%STUDY_ID%}}&SESSION_ID={{%SESSION_ID%}}
```

`SESSION_ID` is one per submission, and the app treats one submission as one
sitting. Someone who finishes, closes the tab and reopens the link gets their
completion link back; a new submission gets the next sitting. Without it every
return visit after a finished sitting starts another one, and the person who
only wanted their link is looking at twenty more minutes of work.

It is stored in `annotations.prolific_session_id`, which a database created
before 2026-11-19 does not have. **The app runs without the column** — it just
cannot make that distinction — so there is no order to get wrong: deploy
whenever, add the column whenever, and the feature switches on at the next
restart after both. `studyuser` cannot add it; run the schema as the admin role
from a DICE desktop (it is idempotent, and a new column needs no new grant):

```bash
ssh s2634187@breezy.inf.ed.ac.uk cat /disk/data/s2634187/llm-playschool/postgres_schema.sql \
  | psql "host=breezy.inf.ed.ac.uk dbname=study user=s2634187"
```

`readiness.py` reports it as blocking until it is there, and
`export_annotations.py` carries the id on every row, to join against
Prolific's own export.

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
systemctl --user status annotation             # is it up? (transient unit, no .service)
tail -f /disk/data/s2634187/app.log            # live logs
systemctl --user restart annotation            # after a config change
crontab -l                                     # backup schedule + the @reboot line
```

Study progress and data checks, from the repo directory:

```bash
.venv/bin/python export_annotations.py --check   # batch coverage, rule violations
.venv/bin/python assignment.py                   # inventory summary + preflight
```

## Backups

`vm/backup_db.sh` runs nightly via cron and writes `study-<stamp>.sql.gz` to
`/disk/data/s2634187/annotation-backups`, keeping 30 days. Not `~`: cron holds
no AFS token. `pg_dump` snapshots inside a single
transaction, so it is safe to run while annotators are working — there is no
one-writer rule to respect any more, unlike the SQLite setup this replaced.

Restore into a scratch database:

```bash
gunzip -c /disk/data/s2634187/annotation-backups/study-<stamp>.sql.gz \
  | psql "host=localhost dbname=study_restore sslmode=require gssencmode=disable"
```

## Troubleshooting, in the order things actually fail

| Symptom | Cause |
|---|---|
| Apache 503 right after a deploy, log path printed as `/afs/...` | `APP_DIR` not pinned — deployed onto AFS, unit dies unable to open its log |
| Apache 503, app not listening | `.env` missing/incomplete → `RuntimeError: Missing required DB config` at import |
| `Refusing to start — the study inventory does not check out` | The corpus `GAMES_DIR` names doesn't match its manifest — rebuild it (`build_study_set.py` / `build_pilot_set.py`) |
| A button does nothing, `dictionary changed size during iteration` in the log | `gradio_patches.py` is not being imported first in `app.py`, or Gradio was upgraded past the pinned version |
| Apache 503, app *is* listening | Port mismatch between the vhost and the app |
| Works until you log out | Linger not enabled |
| Pages load, controls hang | SSE buffered by the proxy — needs `flushpackets=on` |
| `pip` fails installing gradio | System Python older than 3.11; re-run `setup_vm.sh` to get `uv`'s 3.13 |

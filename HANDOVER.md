# Handover

This guide is for the person who takes over the annotation study. It explains
the four jobs you will actually need to do: start the app, get the data, update
the database, and fix the common errors.

`RUNBOOK.md` has the deeper technical detail. Start here.

## 1. What the system is

- The app is a **Gradio** web app written in Python. Annotators come from
  Prolific, read AI game transcripts, and rate them.
- It runs on the university machine **breezy.inf.ed.ac.uk**.
- Answers are saved in a **PostgreSQL** database called `study`, on the same
  machine. Nothing important is saved in normal files, so the data is safe even
  if the app is reinstalled.
- **Apache** is the web server. It receives HTTPS requests and passes them to
  the app on port 3000.

Where things are on breezy:

| What | Where |
| --- | --- |
| The code | `/disk/data/s2634187/llm-playschool` |
| The settings and the database password | `.env` in that folder |
| The transcripts shown to annotators | `games_study/` in that folder |
| The nightly database backups | `/disk/data/s2634187/annotation-backups` |

The `.env` file is **not** in Git, because it holds the password. There is only
one copy, on the machine. Keep a second copy in a password manager.

## 2. Get access first

Informatics machines are not reachable from the open internet. **Connect to the
University VPN before anything else.** If a command below says "no route to
host" or simply waits forever, the VPN is the first thing to check.

```bash
ssh s2634187@breezy.inf.ed.ac.uk
```

If that does not work while you are on campus, go through the gateway:

```bash
ssh -J s2634187@ssh.inf.ed.ac.uk s2634187@breezy.inf.ed.ac.uk
```

## 3. Start, stop and check the app

The app runs as a background service called `annotation`. It restarts itself
automatically if it crashes, so normally you do not need to touch it.

```bash
systemctl --user status annotation      # Is it running?
systemctl --user restart annotation     # Restart it
systemctl --user stop annotation        # Stop it (this pauses the study)
tail -f /disk/data/s2634187/app.log     # Watch the live log. Ctrl+C to leave.
```

Use the log file, not `journalctl` — ordinary users cannot read the journal on
this machine, and `journalctl --user` only reports a permissions error.

One thing to know about this service: it is a **transient** unit. Normally a
service is a file in `~/.config/systemd/user`, but this machine's home
directory is on AFS, and systemd runs without an AFS token, so it cannot read
files there — it reports "Unit file does not exist" while you are looking
straight at the file. The service is therefore registered directly in memory
instead, and a `@reboot` line in `crontab -l` re-creates it after a reboot.
`setup_vm.sh` handles all of this; you only need to know why it looks unusual.

Do **not** start the app by typing `python app.py` yourself. The service is
already using port 3000, so your copy will fail with "address already in use".
It would also stop as soon as you close your terminal.

To install the newest version of the code and restart everything:

```bash
cd /disk/data/s2634187/llm-playschool
bash vm/setup_vm.sh
```

This one command downloads the latest code, checks the settings file, installs
the Python packages, tests the database connection, restarts the service, makes
sure the nightly backup is scheduled, and finally checks that the app answers.
It is safe to run it again at any time. Read its last lines: it tells you
clearly if something is wrong.

## 4. Get the results out

This is the part you will use most. It never changes the data — the export
opens the database in read-only mode — so it is safe to run while annotators
are working.

```bash
cd /disk/data/s2634187/llm-playschool

.venv/bin/python export_annotations.py --check   # Progress only. Writes nothing.
.venv/bin/python export_annotations.py           # Full export into exports/
```

- Use `--check` to see how many annotations are finished and whether any
  batches are still missing. Do this often during collection.
- The full export writes CSV files and a JSON file into the `exports/` folder.
  The questions are written out as real text, not as numbers, so the files stay
  readable later even if the questions in the app change.

To copy the results to your own computer, run this **on your computer** (with
the VPN on):

```bash
scp -r s2634187@breezy.inf.ed.ac.uk:/disk/data/s2634187/llm-playschool/exports ./
```

You can also see how much of the study is covered:

```bash
.venv/bin/python assignment.py                   # Inventory and batch summary
```

## 5. Update the database structure (SQL)

You only need this after a code change that adds a new table or a new column.
You will know, because the app refuses to start and says:

```
Postgres schema is missing table(s) [...], and this DB user lacks CREATE privilege
```

**Why it is not simple.** The app's database user, `studyuser`, is not allowed
to create tables. Only the personal account `s2634187` can. That account is
allowed to connect over IPv6 only, and breezy connects to itself over IPv4. So
this one job cannot be done on breezy. You must do it from another Informatics
desktop, for example `athos`.

```bash
ssh s2634187@athos.inf.ed.ac.uk

klist       # You need a Kerberos ticket. If it says "No credentials cache", run: kinit

curl -sL https://raw.githubusercontent.com/IYURA2006/llm-playschool/main/postgres_schema.sql \
  | psql "host=breezy.inf.ed.ac.uk dbname=study user=s2634187"

curl -sL https://raw.githubusercontent.com/IYURA2006/llm-playschool/main/postgres_grants.sql \
  | psql "host=breezy.inf.ed.ac.uk dbname=study user=s2634187"
```

**Always run both files.** The first one creates the tables. The second one
gives the app permission to use them. If you skip the second file, the app
starts normally and then fails when it tries to save the first answer.

Both files are safe to run many times. They only add what is missing and never
delete anything. Messages like `NOTICE: relation "annotations" already exists,
skipping` are normal and correct.

Then go back to breezy, check that the app's own user can read the new tables,
and restart:

```bash
psql "host=127.0.0.1 dbname=study user=studyuser sslmode=require gssencmode=disable" -c '\dt'
systemctl --user restart annotation
```

Note the different settings in the two commands. Your personal account uses
Kerberos, so it needs GSS encryption. The app's account uses a password, so it
needs `gssencmode=disable`. Using the wrong one gives a confusing
"no pg_hba.conf entry" error that looks like a password problem but is not.

## 6. Backups

A backup runs automatically every night at 03:17. It writes a compressed file
called `study-<date>.sql.gz` into `/disk/data/s2634187/annotation-backups`
and keeps 30 days.
`pg_dump` takes a consistent snapshot, so it is safe even while people are
annotating.

```bash
crontab -l                                        # See the schedule
ls -lh /disk/data/s2634187/annotation-backups     # See the backups
```

To restore one into a separate test database (never into the live one):

```bash
gunzip -c /disk/data/s2634187/annotation-backups/study-<date>.sql.gz \
  | psql "host=localhost dbname=study_restore sslmode=require gssencmode=disable"
```

The backups sit on the machine's local disk, deliberately **not** in the home
directory: cron has no AFS token, so a backup written to `~` would fail every
night without telling anyone. Copy them somewhere else — your own machine or
university storage — from time to time.

## 7. When something goes wrong

| What you see | What it means and what to do |
| --- | --- |
| Website shows **503** | The app is not running. `systemctl --user status annotation`, then restart it. |
| `Missing required DB config` | `.env` is missing or incomplete. It needs `DB_HOST`, `DB_NAME`, `DB_USER`, `DB_PASSWORD`. |
| `Refusing to start — the study inventory does not check out` | `.env` is missing the line `GAMES_DIR=games_study`. Without it the app reads the old pilot transcripts instead of the study ones. |
| `Postgres schema is missing table(s)` | Do section 5. |
| `permission denied for table ...` | Section 5 was done, but the second file (`postgres_grants.sql`) was not. Run it. |
| `address already in use` | The service is already running. Use `restart`, do not start the app by hand. |
| Pages load, but buttons never finish | Apache is buffering. It needs `flushpackets=on` in the site config. Ask computing support. |
| Everything stops when you log out | "Linger" is not enabled. Run `loginctl enable-linger <username>`, or ask computing support. |
| `NumPy was built with baseline optimizations (X86_V2)` | The numpy wheel needs CPU features this machine does not have. Fix: `.venv/bin/pip install "numpy<2"`. |
| `could not open certificate file .../.postgresql/postgresql.crt: Permission denied` | The service is reading an AFS home with no token. Its `HOME` must point at local disk — `setup_vm.sh` sets this. |
| `Unit file annotation.service does not exist` (but the file is there) | Same AFS cause. The service must be a transient unit; re-run `setup_vm.sh`. |

Always read the live log first — it usually names the problem directly:

```bash
tail -50 /disk/data/s2634187/app.log
```

## 8. While Yurii is away

Yurii is on exchange, not leaving — the account stays, and so does everything
in it. Nothing needs to be migrated. The app keeps running on breezy and can be
reached from abroad over the University VPN, so he can still restart it, deploy
changes and pull the data.

What that means in practice:

- For anything routine — restart, logs, export — follow sections 3 to 5. You do
  not need him.
- The code is on GitHub, so changes can be made and reviewed normally.
- The one job that still needs his personal account is a **database structure
  change** (section 5), because only his Kerberos account may create tables.
  Asking computing support for `GRANT CREATE ON SCHEMA public TO studyuser;`
  removes that dependency for good, and is worth doing — otherwise a schema
  change while he is in another timezone waits for him to wake up.
- The Prolific account holds the study and the money. Make sure at least two
  people can get into it.

## 9. Who to contact

- **Informatics computing support** — for the machine, Apache, and the
  database: <https://rt4.inf.ed.ac.uk> or support@inf.ed.ac.uk.
  The database was set up by Graham Dutton under ticket #140791; mentioning
  that ticket gives them the history.
- **Prolific** — the study, the payments and the completion code are in the
  Prolific account. Make sure at least two people can access it.

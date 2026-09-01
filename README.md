# LM Playschool — Annotation Tool

A web app for rating how well AI models play simple games. Built for the
LM Playschool Workshop, a research project on improving language models
through learning from dialogue interaction.

## What this app does

The app shows a human annotator a transcript of an AI playing a game —
for example guessing a word, or negotiating with another AI. The AI's
moves came from [clembench](https://github.com/clembench/clembench), a
framework that runs language models through these games.

The annotator reads each move and answers a few questions about it, such
as:
- Did the AI use information from earlier in the game correctly?
- Was this a sensible next move?
- How clear was the AI's reasoning?

At the end, the annotator also gives an overall score for the whole game.
This data helps researchers understand and improve how well language
models reason and make decisions in interactive settings.

## Who uses it

Annotators join through [Prolific](https://www.prolific.com/) and are
guided through a consent form before starting. The consent form explains
the study, how data is used, and how to withdraw. See `consent.py` for
the full text.

## Quick start

1. Install the dependencies (Python 3.13):
   ```bash
   pip install -r requirements.txt
   ```

2. Copy `.env.example` to `.env` and fill in the real values:
   ```bash
   cp .env.example .env
   ```
   You will need:
   - `DB_HOST`, `DB_NAME`, `DB_USER`, `DB_PASSWORD` — PostgreSQL connection details.
   - `DB_SSLMODE`, `DB_GSSENCMODE` — already set to sensible defaults in `.env.example`.

   On first run, the app creates the database tables itself if your DB
   user has permission to do so. If not, an admin needs to run
   `postgres_schema.sql` against the database first. If the database is
   hosted on the University of Edinburgh's `breezy` server, you need to
   be on the university network or VPN to reach it.

3. Run the app:
   ```bash
   python app.py
   ```
   It opens at `http://localhost:3000` by default — the port Apache on
   the deployment VM proxies to, so leave it alone on a server. To run a
   second copy locally, set `PORT`:
   ```bash
   PORT=3001 python app.py
   ```

Game transcripts under `games/` are already included in this repository —
no extra download is needed.

## Exporting the collected data

`export_annotations.py` turns the database into an analysis-ready snapshot.
It reads the same `DB_*` settings from `.env` as the app, and opens the
connection **read-only**, so it cannot change anything. That means it is
safe to run while the study is still collecting — the coverage section
doubles as a progress monitor.

If the database is on `breezy`, connect to the university VPN first, then
check you can reach it:

```bash
python export_annotations.py --check
```

That prints row counts and exits without writing anything. To produce the
full snapshot:

```bash
python export_annotations.py
```

Each run writes a new timestamped folder under `exports/` (git-ignored,
because the files contain participants' written comments):

| File                 | What it contains                                                              |
|----------------------|-------------------------------------------------------------------------------|
| `annotations.csv`    | One row per transcript per annotator: the final verdict, timings, status.      |
| `turn_ratings.csv`   | One row per rated turn, including the AI message that was rated.               |
| `responses_long.csv` | One row per individual answer, each with the question it answered. Best for analysis — different games ask different questions, and this is the shape that fits them all in one table. |
| `participants.csv`   | One row per annotator: consent, how much they completed, how long they took.   |
| `coverage.csv`       | One row per transcript: how many annotators it has, and how many it still needs. |
| `annotations.json`   | Everything again, nested, with lists and dictionaries kept as real structures. |
| `quality_report.md`  | Coverage, completion and abandonment, durations, rushed or repetitive work, and data-integrity warnings. Read this first. |

Answers are stored in the database as short codes (`"3"`), so the export
translates them back into the question wording and scale labels **as it
runs**, using the definitions in `annotation.py`. Keep each snapshot
folder intact: if the questions are edited later, an old snapshot can no
longer be re-created faithfully.

Two things worth knowing before analysing the data. The `annotator_id`
column holds each participant's Prolific ID as Prolific sends it, so these
files identify participants and should stay within the research team —
treat them like any other personal data, and remove or replace the column
before sharing anything outside it. And re-submitting a game overwrites its
previous answers, so only each annotator's final submission exists.

## Project structure

| File / folder            | What it does                                                        |
|---------------------------|----------------------------------------------------------------------|
| `app.py`                  | Entry point. Wires all screens together and holds the shared CSS/JS. |
| `welcome.py`              | Landing screen with the rating scale and the "Start" button.         |
| `consent.py`               | Consent popup content (participant information sheet).              |
| `training.py`              | A short practice round before the real annotation starts.            |
| `annotation.py`            | The main screen where annotators rate each turn of a game.           |
| `annotation_verdict.py`    | The final screen for the overall game score.                         |
| `assignment.py`            | Picks which games each participant annotates, and keeps coverage balanced. |
| `db.py`                    | Saves annotations to a PostgreSQL database.                          |
| `export_annotations.py`    | Read-only export of the collected annotations, plus a quality report. |
| `games/`                   | Game transcripts (clembench format) shown for annotation.            |
| `postgres_schema.sql`      | Database table definitions.                                          |

## Learn more

- [clembench](https://github.com/clembench/clembench) — the framework that generated the game transcripts

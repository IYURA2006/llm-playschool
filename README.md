---
title: LLM Playschool
emoji: 🚀
colorFrom: blue
colorTo: indigo
sdk: gradio
sdk_version: "6.15.2"
app_file: app.py
pinned: false
---

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
   - `PSEUDONYM_SALT` — any long random string, used to anonymize participant IDs.
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
   It opens at `http://localhost:3000` by default. To use a different
   port, set the `PORT` environment variable:
   ```bash
   PORT=7860 python app.py
   ```

Game transcripts under `games/` are already included in this repository —
no extra download is needed.

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
| `games/`                   | Game transcripts (clembench format) shown for annotation.            |
| `postgres_schema.sql`      | Database table definitions.                                          |

## Learn more

- [clembench](https://github.com/clembench/clembench) — the framework that generated the game transcripts

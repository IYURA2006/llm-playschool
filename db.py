"""SQLite persistence for annotation results.

Annotations are keyed by (game_slug, annotator_id) so each annotator's work on a
game is stored separately. A fresh short-lived connection is opened per operation —
Gradio runs event handlers across threads, and per-call connections sidestep
SQLite's same-thread restriction. WAL mode allows concurrent writers.
"""

import json
import os
import shutil
import sqlite3
from datetime import datetime

from dotenv import load_dotenv

# Load secrets from a local .env (gitignored) for local dev. On an HF Space the
# vars come from the Space's Settings -> Repository secrets, and load_dotenv is a
# harmless no-op there since no .env file is present.
load_dotenv()

_dir = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(_dir, "annotations.db")

# HF Dataset backup target. An HF Space has an ephemeral filesystem, so the local
# annotations.db is wiped on every restart. We mirror it to a private HF dataset
# repo after each verdict submit and restore it on startup. Set HF_DATASET_REPO
# and HF_TOKEN (write access) in .env locally / the Space's Repository secrets.
HF_DATASET_REPO = os.environ.get("HF_DATASET_REPO", "yuriiilnytskyi/playschool-annotations")
_HF_TOKEN = os.environ.get("HF_TOKEN")

_SCHEMA = """
CREATE TABLE IF NOT EXISTS annotations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    game_slug TEXT NOT NULL,
    game_id INTEGER,
    game_name TEXT,
    source_path TEXT,
    has_reasoning INTEGER,
    annotator_id TEXT NOT NULL DEFAULT '',
    annotated_at TEXT,
    strategic_coherence TEXT,
    overall_rating INTEGER,
    verdict_comment TEXT,
    verdict_at TEXT,
    updated_at TEXT,
    UNIQUE(game_slug, annotator_id)
);

CREATE TABLE IF NOT EXISTS turn_ratings (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    annotation_id INTEGER NOT NULL REFERENCES annotations(id) ON DELETE CASCADE,
    turn_index INTEGER NOT NULL,
    from_player TEXT,
    role TEXT,
    content TEXT,
    prior_information_use TEXT,
    strategic_logic TEXT,
    reasoning_clarity TEXT,
    flags TEXT,
    comment TEXT,
    UNIQUE(annotation_id, turn_index)
);
"""


def _connect():
    conn = sqlite3.connect(DB_PATH)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def init_db():
    with _connect() as conn:
        conn.executescript(_SCHEMA)


def backup_db_to_hf():
    """Best-effort push of annotations.db to the HF dataset repo. Never raises."""
    if not _HF_TOKEN:
        return
    try:
        # Flush WAL into the main file so the upload is complete (WAL mode).
        with _connect() as conn:
            conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        from huggingface_hub import HfApi
        HfApi(token=_HF_TOKEN).upload_file(
            path_or_fileobj=DB_PATH,
            path_in_repo="annotations.db",
            repo_id=HF_DATASET_REPO,
            repo_type="dataset",
        )
    except Exception as e:
        # Never let a backup failure break the annotator's submit flow.
        print(f"⚠️ HF backup failed: {e}")


def _restore_db_from_hf():
    """If no local DB exists, pull the last backup from the HF dataset repo."""
    if os.path.exists(DB_PATH) or not _HF_TOKEN:
        return
    try:
        from huggingface_hub import hf_hub_download
        downloaded = hf_hub_download(
            repo_id=HF_DATASET_REPO,
            filename="annotations.db",
            repo_type="dataset",
            token=_HF_TOKEN,
        )
        shutil.copy(downloaded, DB_PATH)
        print("✅ Restored annotations.db from HF dataset backup")
    except Exception as e:
        print(f"No existing HF backup found, starting fresh: {e}")


def save_turns(game_slug, meta, source_path, has_reasoning, annotator_id, turns_out):
    """Upsert the annotation row and replace its turn ratings. Returns annotation id."""
    now = datetime.now().isoformat()
    annotator_id = annotator_id or ""
    with _connect() as conn:
        cur = conn.cursor()
        cur.execute(
            """
            INSERT INTO annotations
                (game_slug, game_id, game_name, source_path, has_reasoning,
                 annotator_id, annotated_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(game_slug, annotator_id) DO UPDATE SET
                game_id=excluded.game_id,
                game_name=excluded.game_name,
                source_path=excluded.source_path,
                has_reasoning=excluded.has_reasoning,
                annotated_at=excluded.annotated_at,
                updated_at=excluded.updated_at
            """,
            (
                game_slug,
                meta.get("game_id"),
                meta.get("game_name"),
                source_path,
                1 if has_reasoning else 0,
                annotator_id,
                now,
                now,
            ),
        )
        cur.execute(
            "SELECT id FROM annotations WHERE game_slug=? AND annotator_id=?",
            (game_slug, annotator_id),
        )
        annotation_id = cur.fetchone()[0]

        cur.execute("DELETE FROM turn_ratings WHERE annotation_id=?", (annotation_id,))
        cur.executemany(
            """
            INSERT INTO turn_ratings
                (annotation_id, turn_index, from_player, role, content,
                 prior_information_use, strategic_logic, reasoning_clarity,
                 flags, comment)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                (
                    annotation_id,
                    t["turn_index"],
                    t["from"],
                    t["role"],
                    t["content"],
                    t["prior_information_use"],
                    t["strategic_logic"],
                    t["reasoning_clarity"],
                    json.dumps(t["flags"]),
                    t["comment"],
                )
                for t in turns_out
            ],
        )
    return annotation_id


def save_verdict(game_slug, annotator_id, coherence, overall, comment):
    """Update the verdict columns of an existing annotation row.

    Returns True if a matching row existed (turns submitted first), else False.
    """
    now = datetime.now().isoformat()
    annotator_id = annotator_id or ""
    with _connect() as conn:
        cur = conn.execute(
            """
            UPDATE annotations SET
                strategic_coherence=?,
                overall_rating=?,
                verdict_comment=?,
                verdict_at=?,
                updated_at=?
            WHERE game_slug=? AND annotator_id=?
            """,
            (coherence, overall, comment, now, now, game_slug, annotator_id),
        )
        ok = cur.rowcount > 0
    # save_turns wrote the turn rows before this verdict step, so backing up now
    # captures both turns and verdict in a single push.
    if ok:
        backup_db_to_hf()
    return ok


_restore_db_from_hf()   # pull backup first (no-op if local DB present)
init_db()               # then ensure schema exists (CREATE TABLE IF NOT EXISTS)

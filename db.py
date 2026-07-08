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
import threading
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
# repo after each submit and restore it on startup.
#
# THIS BRANCH ONLY READS HF_PILOT_DATASET_REPO — never HF_DATASET_REPO. The old
# fallback to the production dataset meant any local run with the developer's
# .env (which holds a live write token and no pilot var) silently overwrote the
# production backup with local test data — this actually happened once and had
# to be restored from the HF dataset's commit history. Unset => backup/restore
# are disabled outright; the pilot branch must never be able to touch production.
HF_DATASET_REPO = os.environ.get("HF_PILOT_DATASET_REPO")
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
    condition TEXT,
    session_day TEXT,
    session_started_at TEXT,
    started_at TEXT,
    annotated_at TEXT,
    strategic_coherence TEXT,
    overall_rating INTEGER,
    verdict_comment TEXT,
    verdict_at TEXT,
    survey_fit TEXT,
    survey_fatigue TEXT,
    survey_confidence TEXT,
    survey_comment TEXT,
    updated_at TEXT,
    UNIQUE(game_slug, annotator_id, condition)
);

CREATE TABLE IF NOT EXISTS session_surveys (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    annotator_id TEXT NOT NULL DEFAULT '',
    session_day TEXT,
    capacity TEXT,
    disruption TEXT,
    guessing_preference TEXT,
    would_change_answers TEXT,
    comment TEXT,
    submitted_at TEXT,
    UNIQUE(annotator_id, session_day)
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
    extra_responses TEXT,
    UNIQUE(annotation_id, turn_index)
);
"""


def _connect():
    conn = sqlite3.connect(DB_PATH)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    # WAL allows only one writer; without a busy_timeout a second concurrent
    # writer (two annotators submitting at once) gets SQLITE_BUSY immediately,
    # which propagates as an uncaught OperationalError and the save is lost.
    conn.execute("PRAGMA busy_timeout=10000")
    return conn


def _migrate_pilot_schema(conn):
    """One-time upgrade for pre-pilot annotations.db files: add `condition` /
    `extra_responses` and widen the annotations UNIQUE key to include
    `condition` (so the same annotator+game under a different pilot block
    doesn't silently overwrite prior data). No-op once already migrated;
    SQLite can't ALTER a UNIQUE constraint in place, hence the table rebuild.
    """
    cols = {row[1] for row in conn.execute("PRAGMA table_info(annotations)")}
    if "condition" in cols:
        return  # already migrated (or a fresh pilot DB, created with the schema above)

    conn.execute("ALTER TABLE annotations ADD COLUMN condition TEXT")
    turn_cols = {row[1] for row in conn.execute("PRAGMA table_info(turn_ratings)")}
    if "extra_responses" not in turn_cols:
        conn.execute("ALTER TABLE turn_ratings ADD COLUMN extra_responses TEXT")

    conn.execute("""
        CREATE TABLE annotations_new (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            game_slug TEXT NOT NULL,
            game_id INTEGER,
            game_name TEXT,
            source_path TEXT,
            has_reasoning INTEGER,
            annotator_id TEXT NOT NULL DEFAULT '',
            condition TEXT,
            annotated_at TEXT,
            strategic_coherence TEXT,
            overall_rating INTEGER,
            verdict_comment TEXT,
            verdict_at TEXT,
            updated_at TEXT,
            UNIQUE(game_slug, annotator_id, condition)
        )
    """)
    conn.execute("""
        INSERT INTO annotations_new
            (id, game_slug, game_id, game_name, source_path, has_reasoning,
             annotator_id, condition, annotated_at, strategic_coherence,
             overall_rating, verdict_comment, verdict_at, updated_at)
        SELECT id, game_slug, game_id, game_name, source_path, has_reasoning,
               annotator_id, condition, annotated_at, strategic_coherence,
               overall_rating, verdict_comment, verdict_at, updated_at
        FROM annotations
    """)
    conn.execute("DROP TABLE annotations")
    conn.execute("ALTER TABLE annotations_new RENAME TO annotations")


def _migrate_ab_columns(conn):
    """Additive columns for the internal A/B pilot: session timing and the
    per-game questions-fit micro-survey. Safe to run repeatedly.

    survey_fit/survey_fatigue are no longer written by the app (replaced by
    survey_fit_missing/survey_fit_missing_count) but are kept here and in the
    schema as harmless historical columns — an active pilot DB may already
    hold real Day-1 answers in them, and this migration path is additive-only
    by design (no DROP COLUMN)."""
    cols = {row[1] for row in conn.execute("PRAGMA table_info(annotations)")}
    for col in ("session_day", "session_started_at", "started_at", "survey_fit",
                "survey_fatigue", "survey_confidence", "survey_comment",
                "survey_fit_missing", "survey_fit_missing_count",
                # Hybrid-only per-game whole-game ("specific overall") answers,
                # stored as a JSON object {question_index: value}. Additive/
                # nullable so it is safe on the live production dataset.
                "verdict_specific"):
        if col not in cols:
            conn.execute(f"ALTER TABLE annotations ADD COLUMN {col} TEXT")


def init_db():
    with _connect() as conn:
        conn.executescript(_SCHEMA)
        _migrate_pilot_schema(conn)
        _migrate_ab_columns(conn)


# Serializes snapshot+upload: concurrent submits must not interleave uploads
# (stale-over-fresh races) or snapshot while another backup is mid-flight.
_backup_lock = threading.Lock()


def backup_db_to_hf():
    """Best-effort push of annotations.db to the HF dataset repo. Never raises."""
    if not HF_DATASET_REPO:
        print("⚠️ HF backup DISABLED: HF_PILOT_DATASET_REPO is not set — the pilot "
              "branch never writes the production dataset. Annotations live only "
              "in the local annotations.db until the secret is configured.")
        return
    if not _HF_TOKEN:
        # Loud, because a missing token silently drops every annotation: the
        # annotator sees "saved" while nothing reaches the HF dataset backup.
        print("⚠️ HF backup SKIPPED: HF_TOKEN is not set — annotation NOT persisted "
              "to the HF dataset. Set HF_TOKEN in the Space's Repository secrets.")
        return
    snapshot = DB_PATH + ".hfsnapshot"
    with _backup_lock:
        try:
            # Point-in-time copy via the sqlite3 backup API. Uploading the live
            # file (the old approach) races SQLite's automatic checkpoints —
            # a concurrent commit can rewrite the main file mid-upload, giving
            # the HF copy torn pages or missing the newest WAL-only commits.
            src = _connect()
            try:
                dst = sqlite3.connect(snapshot)
                try:
                    src.backup(dst)
                finally:
                    dst.close()
            finally:
                src.close()
            from huggingface_hub import HfApi
            HfApi(token=_HF_TOKEN).upload_file(
                path_or_fileobj=snapshot,
                path_in_repo="annotations.db",
                repo_id=HF_DATASET_REPO,
                repo_type="dataset",
            )
        except Exception as e:
            # Never let a backup failure break the annotator's submit flow.
            print(f"⚠️ HF backup failed: {e}")
        finally:
            for suffix in ("", "-wal", "-shm"):
                try:
                    os.remove(snapshot + suffix)
                except OSError:
                    pass


def _restore_db_from_hf():
    """If no local DB exists, pull the last backup from the HF dataset repo."""
    if os.path.exists(DB_PATH):
        return
    if not HF_DATASET_REPO:
        print("⚠️ HF restore DISABLED: HF_PILOT_DATASET_REPO is not set — starting "
              "with an empty local DB; the pilot branch never reads the "
              "production dataset.")
        return
    if not _HF_TOKEN:
        print("⚠️ HF restore SKIPPED: HF_TOKEN is not set — starting with an empty DB "
              "and annotations will NOT be backed up. Set HF_TOKEN in the Space's "
              "Repository secrets.")
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


def save_turns(game_slug, meta, source_path, has_reasoning, annotator_id, condition,
               turns_out, started_at=None, session_day=None, session_started_at=None):
    """Upsert the annotation row and replace its turn ratings. Returns annotation id."""
    now = datetime.now().isoformat()
    annotator_id = annotator_id or ""
    condition = condition or ""
    with _connect() as conn:
        cur = conn.cursor()
        cur.execute(
            """
            INSERT INTO annotations
                (game_slug, game_id, game_name, source_path, has_reasoning,
                 annotator_id, condition, session_day, session_started_at,
                 started_at, annotated_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(game_slug, annotator_id, condition) DO UPDATE SET
                game_id=excluded.game_id,
                game_name=excluded.game_name,
                source_path=excluded.source_path,
                has_reasoning=excluded.has_reasoning,
                session_day=excluded.session_day,
                session_started_at=COALESCE(excluded.session_started_at,
                                            annotations.session_started_at),
                started_at=COALESCE(excluded.started_at, annotations.started_at),
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
                condition,
                session_day,
                session_started_at,
                started_at,
                now,
                now,
            ),
        )
        cur.execute(
            "SELECT id FROM annotations WHERE game_slug=? AND annotator_id=? AND condition=?",
            (game_slug, annotator_id, condition),
        )
        annotation_id = cur.fetchone()[0]

        cur.execute("DELETE FROM turn_ratings WHERE annotation_id=?", (annotation_id,))
        cur.executemany(
            """
            INSERT INTO turn_ratings
                (annotation_id, turn_index, from_player, role, content,
                 prior_information_use, strategic_logic, reasoning_clarity,
                 flags, comment, extra_responses)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                    json.dumps(t["extra_responses"]) if t.get("extra_responses") else None,
                )
                for t in turns_out
            ],
        )
    # Pilot: back up on every turn submission (not just the final verdict), so
    # a session abandoned mid-way is still captured within the shortened window.
    backup_db_to_hf()
    return annotation_id


def completed_pairs(annotator_id):
    """{(game_slug, condition)} this annotator has fully finished (verdict
    submitted). Drives the welcome form's resume logic — a game with turns
    saved but no verdict counts as not done and will be redone."""
    with _connect() as conn:
        rows = conn.execute(
            "SELECT game_slug, condition FROM annotations "
            "WHERE annotator_id=? AND verdict_at IS NOT NULL",
            (annotator_id or "",),
        ).fetchall()
    return {(r[0], r[1]) for r in rows}


def save_verdict(game_slug, annotator_id, condition, coherence, overall, comment,
                 survey_confidence=None, survey_comment=None,
                 survey_fit_missing=None, survey_fit_missing_count=None,
                 verdict_specific=None):
    """Update the verdict columns of an existing annotation row.

    `verdict_specific` is the hybrid-only per-game whole-game answer(s), passed
    as a JSON string (or None); universal-condition saves leave it NULL.

    Returns True if a matching row existed (turns submitted first), else False.
    """
    now = datetime.now().isoformat()
    annotator_id = annotator_id or ""
    condition = condition or ""
    with _connect() as conn:
        cur = conn.execute(
            """
            UPDATE annotations SET
                strategic_coherence=?,
                overall_rating=?,
                verdict_comment=?,
                verdict_at=?,
                survey_confidence=?,
                survey_comment=?,
                survey_fit_missing=?,
                survey_fit_missing_count=?,
                verdict_specific=?,
                updated_at=?
            WHERE game_slug=? AND annotator_id=? AND condition=?
            """,
            (coherence, overall, comment, now, survey_confidence, survey_comment,
             survey_fit_missing, survey_fit_missing_count, verdict_specific, now,
             game_slug, annotator_id, condition),
        )
        ok = cur.rowcount > 0
    # save_turns already backs up; back up again here so the verdict fields
    # (submitted after turns, in a separate step) are captured too.
    if ok:
        backup_db_to_hf()
    return ok


def save_session_survey(annotator_id, session_day, capacity, disruption,
                        guessing_preference, would_change_answers, comment=""):
    """Upsert the once-per-sitting end-of-session survey (asked after the
    last game of a playlist, not tied to any single annotation row)."""
    now = datetime.now().isoformat()
    annotator_id = annotator_id or ""
    session_day = session_day or ""
    with _connect() as conn:
        conn.execute(
            """
            INSERT INTO session_surveys
                (annotator_id, session_day, capacity, disruption,
                 guessing_preference, would_change_answers, comment, submitted_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(annotator_id, session_day) DO UPDATE SET
                capacity=excluded.capacity,
                disruption=excluded.disruption,
                guessing_preference=excluded.guessing_preference,
                would_change_answers=excluded.would_change_answers,
                comment=excluded.comment,
                submitted_at=excluded.submitted_at
            """,
            (annotator_id, session_day, capacity, disruption, guessing_preference,
             would_change_answers, comment, now),
        )
    backup_db_to_hf()
    return True


print(f"🗄️  DB config: HF_TOKEN={'set' if _HF_TOKEN else 'MISSING'}, "
      f"HF_PILOT_DATASET_REPO={HF_DATASET_REPO or 'UNSET — cloud backup/restore disabled'}")
_restore_db_from_hf()   # pull backup first (no-op if local DB present)
init_db()               # then ensure schema exists (CREATE TABLE IF NOT EXISTS)

"""PostgreSQL persistence for annotation results, keyed by
(game_slug, annotator_id, condition). A fresh connection is opened per
operation and closed on exit, since Gradio runs handlers across threads."""

import contextlib
import hashlib
import hmac
import json
import os
from datetime import datetime

import psycopg2
from psycopg2 import errors as pg_errors
from dotenv import load_dotenv

# No-op on HF Spaces (no .env file there); vars come from repository secrets instead.
load_dotenv()

DB_HOST = os.environ.get("DB_HOST")
DB_NAME = os.environ.get("DB_NAME")
DB_USER = os.environ.get("DB_USER")
DB_PASSWORD = os.environ.get("DB_PASSWORD")
DB_SSLMODE = os.environ.get("DB_SSLMODE", "require")
DB_GSSENCMODE = os.environ.get("DB_GSSENCMODE", "disable")

_PSEUDONYM_SALT = os.environ.get("PSEUDONYM_SALT")


def _require_db_config():
    missing = [name for name, val in (
        ("DB_HOST", DB_HOST), ("DB_NAME", DB_NAME),
        ("DB_USER", DB_USER), ("DB_PASSWORD", DB_PASSWORD),
    ) if not val]
    if missing:
        raise RuntimeError(
            f"Missing required DB config: {', '.join(missing)}. Set them in .env "
            "(or the deployment's repository secrets) before the app can start."
        )


def pseudonymize_pid(raw_pid):
    """Deterministic pseudonym for a raw Prolific PID — the raw PID is never
    stored. Raises rather than falling back to an unsalted hash."""
    if not _PSEUDONYM_SALT:
        raise RuntimeError(
            "PSEUDONYM_SALT is not set — required before any real Prolific "
            "PID may be processed. Set it in .env (or the deployment's "
            "repository secrets)."
        )
    return hmac.new(_PSEUDONYM_SALT.encode(), raw_pid.encode(),
                     hashlib.sha256).hexdigest()[:16]


_SCHEMA = """
CREATE TABLE IF NOT EXISTS annotations (
    id INTEGER GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    game_slug TEXT NOT NULL,
    game_id INTEGER,
    game_name TEXT,
    source_path TEXT,
    has_reasoning BOOLEAN,
    annotator_id TEXT NOT NULL DEFAULT '',
    condition TEXT,
    -- Which sitting this reservation belongs to (1, 2, … up to
    -- assignment.MAX_SESSIONS); each batch is disjoint from earlier ones.
    session_index INTEGER,
    session_day TEXT,
    session_started_at TIMESTAMP,
    started_at TIMESTAMP,
    annotated_at TIMESTAMP,
    strategic_coherence TEXT,
    overall_rating INTEGER,
    verdict_comment TEXT,
    verdict_at TIMESTAMP,
    verdict_specific TEXT,
    updated_at TIMESTAMP,
    UNIQUE(game_slug, annotator_id, condition)
);

CREATE TABLE IF NOT EXISTS turn_ratings (
    id INTEGER GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
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

CREATE TABLE IF NOT EXISTS consents (
    id INTEGER GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    annotator_id TEXT NOT NULL UNIQUE,
    consented_at TIMESTAMP NOT NULL
);
"""


def _open():
    return psycopg2.connect(
        host=DB_HOST,
        dbname=DB_NAME,
        user=DB_USER,
        password=DB_PASSWORD,
        sslmode=DB_SSLMODE,
        gssencmode=DB_GSSENCMODE,
    )


@contextlib.contextmanager
def _connect():
    conn = _open()
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def _check_schema_exists():
    with _connect() as conn:
        cur = conn.cursor()
        cur.execute(
            "SELECT to_regclass('public.annotations'), "
            "to_regclass('public.turn_ratings'), to_regclass('public.consents')"
        )
        row = cur.fetchone()
    missing = [name for name, present in zip(
        ("annotations", "turn_ratings", "consents"), row) if present is None]
    if missing:
        raise RuntimeError(
            f"Postgres schema is missing table(s) {missing}, and this DB user "
            "lacks CREATE privilege to create them itself. Run "
            "postgres_schema.sql as an admin role against breezy/study first, "
            "then restart the app."
        )


def init_db():
    """Create the schema if this DB role can; otherwise verify it already
    exists via postgres_schema.sql and fail loudly if not."""
    try:
        with _connect() as conn:
            cur = conn.cursor()
            cur.execute(_SCHEMA)
    except pg_errors.InsufficientPrivilege:
        _check_schema_exists()


# Fixed key for the advisory lock guarding assignment.py's read-decide-write
# reservation sequence (see write_transaction below).
_ASSIGNMENT_LOCK_KEY = 1


@contextlib.contextmanager
def write_transaction():
    """Yield a connection holding an advisory lock, so assignment.py's
    read-coverage / decide / reserve sequence is atomic even under many
    concurrent Prolific requests. Only blocks other write_transaction()
    calls — plain writes elsewhere aren't affected."""
    conn = _open()
    try:
        cur = conn.cursor()
        cur.execute("SELECT pg_advisory_xact_lock(%s)", (_ASSIGNMENT_LOCK_KEY,))
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


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
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
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
                bool(has_reasoning),
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
            "SELECT id FROM annotations WHERE game_slug=%s AND annotator_id=%s AND condition=%s",
            (game_slug, annotator_id, condition),
        )
        annotation_id = cur.fetchone()[0]

        cur.execute("DELETE FROM turn_ratings WHERE annotation_id=%s", (annotation_id,))
        cur.executemany(
            """
            INSERT INTO turn_ratings
                (annotation_id, turn_index, from_player, role, content,
                 prior_information_use, strategic_logic, reasoning_clarity,
                 flags, comment, extra_responses)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
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
    return annotation_id


def completed_pairs(annotator_id):
    """{(game_slug, condition)} this annotator has fully finished (verdict
    submitted). A game with turns saved but no verdict counts as not done."""
    with _connect() as conn:
        cur = conn.cursor()
        cur.execute(
            "SELECT game_slug, condition FROM annotations "
            "WHERE annotator_id=%s AND verdict_at IS NOT NULL",
            (annotator_id or "",),
        )
        rows = cur.fetchall()
    return {(r[0], r[1]) for r in rows}


def has_consented(annotator_id):
    """Whether this identity has already accepted the consent form. An empty
    annotator_id (identity not yet resolved) can never have a consent
    record, so this returns False without hitting the DB."""
    if not annotator_id:
        return False
    with _connect() as conn:
        cur = conn.cursor()
        cur.execute("SELECT 1 FROM consents WHERE annotator_id=%s", (annotator_id,))
        row = cur.fetchone()
    return row is not None


def record_consent(annotator_id):
    """Record that `annotator_id` accepted the consent form, once. A no-op
    for an empty annotator_id — nothing to key the record on yet."""
    if not annotator_id:
        return
    now = datetime.now().isoformat()
    with _connect() as conn:
        cur = conn.cursor()
        cur.execute(
            "INSERT INTO consents (annotator_id, consented_at) VALUES (%s, %s) "
            "ON CONFLICT(annotator_id) DO NOTHING",
            (annotator_id, now),
        )


def assigned_games(annotator_id, condition=None, session_index=None, conn=None):
    """[{"game": slug, "condition": condition}, …] reserved for this PID, in
    order — the source of truth so a returning PID gets the same playlist
    back, not a freshly recomputed one. Pass session_index to scope to one
    sitting, or a returning participant would replay earlier games."""
    q = "SELECT game_slug, condition FROM annotations WHERE annotator_id=%s"
    params = [annotator_id or ""]
    if condition is not None:
        q += " AND condition=%s"
        params.append(condition)
    if session_index is not None:
        q += " AND COALESCE(session_index, 1)=%s"
        params.append(session_index)
    q += " ORDER BY id"
    if conn is not None:
        cur = conn.cursor()
        cur.execute(q, params)
        rows = cur.fetchall()
    else:
        with _connect() as c:
            cur = c.cursor()
            cur.execute(q, params)
            rows = cur.fetchall()
    return [{"game": r[0], "condition": r[1]} for r in rows]


def reserved_slugs(annotator_id, conn=None):
    """Every game_slug this participant has ever been reserved — drives the
    no-repeat rule. Not filtered by condition: a repeat viewing under a
    different question set is still a repeat."""
    q = "SELECT DISTINCT game_slug FROM annotations WHERE annotator_id=%s"
    params = [annotator_id or ""]
    if conn is not None:
        cur = conn.cursor()
        cur.execute(q, params)
        rows = cur.fetchall()
    else:
        with _connect() as c:
            cur = c.cursor()
            cur.execute(q, params)
            rows = cur.fetchall()
    return {r[0] for r in rows}


def session_summary(annotator_id, condition=None, conn=None):
    """[(session_index, n_games, n_with_verdict), …] ascending, for this PID.
    A session is complete only once every reserved game has a verdict."""
    q = ("SELECT COALESCE(session_index, 1) AS s, COUNT(*), "
         "       COUNT(verdict_at) "
         "FROM annotations WHERE annotator_id=%s")
    params = [annotator_id or ""]
    if condition is not None:
        q += " AND condition=%s"
        params.append(condition)
    q += " GROUP BY s ORDER BY s"
    if conn is not None:
        cur = conn.cursor()
        cur.execute(q, params)
        rows = cur.fetchall()
    else:
        with _connect() as c:
            cur = c.cursor()
            cur.execute(q, params)
            rows = cur.fetchall()
    return [(int(r[0]), int(r[1]), int(r[2])) for r in rows]


def coverage_counts(condition, stale_before=None, conn=None):
    """{game_slug: distinct-annotator count} under `condition`. Counts a row
    if it has a verdict, or — when `stale_before` is given — if it was
    touched more recently than that. `stale_before` must be a naive-local
    ISO string, same format as every other timestamp here."""
    sql = "SELECT game_slug, COUNT(DISTINCT annotator_id) FROM annotations WHERE condition=%s"
    params = [condition]
    if stale_before is not None:
        sql += " AND (verdict_at IS NOT NULL OR updated_at > %s)"
        params.append(stale_before)
    else:
        sql += " AND verdict_at IS NOT NULL"
    sql += " GROUP BY game_slug"
    if conn is not None:
        cur = conn.cursor()
        cur.execute(sql, params)
        rows = cur.fetchall()
    else:
        with _connect() as c:
            cur = c.cursor()
            cur.execute(sql, params)
            rows = cur.fetchall()
    return dict(rows)


def reserve_games(annotator_id, condition, game_slugs, session_index=1, conn=None):
    """Claim `game_slugs` for `annotator_id` under `condition` as placeholder
    rows; save_turns fills in the rest on first submit. A slug already
    reserved in an earlier session is silently skipped, so callers must
    exclude already-reserved slugs when picking (db.reserved_slugs())."""
    game_slugs = list(game_slugs)
    if not game_slugs:
        return
    now = datetime.now().isoformat()
    rows = [(slug, annotator_id or "", condition, session_index, now)
            for slug in game_slugs]
    sql = ("INSERT INTO annotations "
           "    (game_slug, annotator_id, condition, session_index, updated_at) "
           "VALUES (%s, %s, %s, %s, %s) "
           "ON CONFLICT(game_slug, annotator_id, condition) DO NOTHING")
    if conn is not None:
        cur = conn.cursor()
        cur.executemany(sql, rows)
        return
    with _connect() as c:
        cur = c.cursor()
        cur.executemany(sql, rows)


def save_verdict(game_slug, annotator_id, condition, coherence, overall, comment,
                 verdict_specific=None):
    """Update the verdict columns of an existing annotation row. Returns True
    if a matching row existed (turns submitted first), else False."""
    now = datetime.now().isoformat()
    annotator_id = annotator_id or ""
    condition = condition or ""
    with _connect() as conn:
        cur = conn.cursor()
        cur.execute(
            """
            UPDATE annotations SET
                strategic_coherence=%s,
                overall_rating=%s,
                verdict_comment=%s,
                verdict_at=%s,
                verdict_specific=%s,
                updated_at=%s
            WHERE game_slug=%s AND annotator_id=%s AND condition=%s
            """,
            (coherence, overall, comment, now, verdict_specific, now,
             game_slug, annotator_id, condition),
        )
        ok = cur.rowcount > 0
    return ok


_require_db_config()
init_db()

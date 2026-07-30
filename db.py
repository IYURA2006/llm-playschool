"""PostgreSQL persistence for annotation results.

Annotations are keyed by (game_slug, annotator_id) so each annotator's work on a
game is stored separately. A fresh connection is opened per operation and always
closed on exit — Gradio runs event handlers across threads, so no connection is
ever shared across calls.
"""

import contextlib
import hashlib
import hmac
import json
import os
from datetime import datetime

import psycopg2
from psycopg2 import errors as pg_errors
from dotenv import load_dotenv

# Load secrets from a local .env (gitignored) for local dev. On an HF Space the
# vars come from the Space's Settings -> Repository secrets, and load_dotenv is a
# harmless no-op there since no .env file is present.
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
    """Deterministic participant identifier derived from a raw Prolific PID —
    the same raw PID always maps to the same pseudonym (required for
    assignment.py's multi-session resume), but the raw PID itself is never
    stored. Fails loudly if PSEUDONYM_SALT is unset rather than silently
    falling back to an unsalted hash, which would defeat the point."""
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
    -- Which sitting this reservation belongs to: 1 for a participant's first
    -- session, 2 for their second, … up to assignment.MAX_SESSIONS. A
    -- returning Prolific PID gets a fresh batch under the next index, and
    -- every batch is disjoint from the ones before it (assignment.py excludes
    -- already-reserved slugs), which is what makes the session-agnostic
    -- completed_pairs() still resolve resume position correctly.
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
    """Create the schema if this DB role has CREATE privilege on schema
    public (e.g. a fresh local/dev database); otherwise studyuser-style
    deployments only have DML privileges, so fall back to verifying the
    schema already exists (created via the one-time postgres_schema.sql
    step) and fail loudly if it doesn't."""
    try:
        with _connect() as conn:
            cur = conn.cursor()
            cur.execute(_SCHEMA)
    except pg_errors.InsufficientPrivilege:
        _check_schema_exists()


# A fixed key: serializes assignment.py's read-decide-write claim sequence
# the same way SQLite's BEGIN IMMEDIATE did (see write_transaction below).
_ASSIGNMENT_LOCK_KEY = 1


@contextlib.contextmanager
def write_transaction():
    """Yield a connection holding a session-scoped advisory lock, for callers
    needing an atomic read-decide-write sequence across multiple statements —
    e.g. assignment.py's coverage-balanced reservation: read current per-game
    coverage counts, decide what to reserve, insert the reservation, all
    atomically. Without this, two concurrent callers could both read the same
    under-target count and both reserve past the coverage target (a Prolific
    study "release" moment is exactly this kind of thundering-herd scenario).

    pg_advisory_xact_lock blocks a second concurrent write_transaction() until
    this one commits or rolls back, at which point Postgres releases the lock
    automatically — no separate unlock call needed. Scoped to just this claim
    path: unlike SQLite's BEGIN IMMEDIATE (which serialized every write in the
    whole database, a side effect of SQLite's single-writer limitation, not a
    deliberate choice), plain writes elsewhere (save_turns, save_verdict,
    record_consent) are not blocked by or against this lock."""
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
    submitted). Drives the welcome form's resume logic — a game with turns
    saved but no verdict counts as not done and will be redone."""
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
    """Whether this identity has already accepted the consent form — an
    empty annotator_id (identity not resolved yet, e.g. a bare-URL visitor
    who hasn't picked a name) can never have a consent record, by
    construction, so this is always False for it rather than hitting the DB."""
    if not annotator_id:
        return False
    with _connect() as conn:
        cur = conn.cursor()
        cur.execute("SELECT 1 FROM consents WHERE annotator_id=%s", (annotator_id,))
        row = cur.fetchone()
    return row is not None


def record_consent(annotator_id):
    """Record that `annotator_id` accepted the consent form, once. A no-op
    for an empty annotator_id (nothing to key the record on yet) — the
    consent screen still gates the UI client-side in that case, it just
    isn't persisted until an identity exists."""
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
    """[{"game": slug, "condition": condition}, …] already reserved for this
    annotator/PID, in reservation order (insertion order == id order, since
    reserve_games is the only inserter for a brand-new pair). The source of
    truth for rebuilding a playlist on any reload/resume — a returning PID
    must get back the SAME playlist, not a freshly recomputed one, both
    because re-picking would defeat the reservation-as-claim mechanism and
    because it must match whatever condition a resumed session used.

    `session_index` narrows the result to ONE sitting. Pass it whenever the
    result will be used as a playlist: a returning participant has several
    sessions' worth of reservations on file, and handing back all of them
    would replay games they already finished. Omit it only for
    whole-participant questions (e.g. the no-repeat exclusion set).
    NULL session_index rows (reserved before multi-session support existed)
    are treated as session 1."""
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
    """Every game_slug this participant has EVER been reserved, across all
    sessions and conditions. Drives the no-repeat rule: a returning PID must
    never be handed a transcript they have already seen, in any earlier
    sitting. Deliberately not filtered by condition — seeing the same
    transcript again under a different question set would still be a repeat
    viewing, and the second rating could not be treated as independent."""
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

    Everything the cap and the resume decision need in one read: how many
    sittings exist, and whether the latest one is finished. A session counts
    as COMPLETED only when every reserved game in it carries a verdict —
    a half-finished sitting must resume, not burn one of the participant's
    allowed sessions."""
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
    """{game_slug: distinct-annotator count} under `condition`. A row counts
    as "covering" its slug if it has a verdict, OR — when `stale_before` is
    given — if it was touched (reserved, turns saved, or verdicted) more
    recently than `stale_before`.

    `stale_before` MUST be an ISO string produced the same way every other
    timestamp in this module is (datetime.now().isoformat() — naive local
    time), e.g. via assignment._stale_cutoff(). Postgres casts both sides of
    the comparison to a real TIMESTAMP, so this is a genuine temporal
    comparison (not the lexicographic string comparison SQLite required care
    around) — it just still needs both sides to be naive-local, consistently,
    since the column type has no time zone attached.

    Omit `stale_before` for a pure "verdicted only" count (e.g. a final
    coverage report, where in-flight reservations shouldn't count)."""
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
    """Claim `game_slugs` for `annotator_id` under `condition`: bare
    placeholder annotation rows (game_id/game_name/source_path/has_reasoning
    left NULL — save_turns' own upsert fills them in on first turn-submit,
    same as it always has). Reuses UNIQUE(game_slug, annotator_id, condition)
    via ON CONFLICT DO NOTHING so re-calling with an already-reserved slug is
    a no-op, not an error or a duplicate.

    `session_index` records which of the participant's sittings this batch
    belongs to. The ON CONFLICT clause means a slug already reserved under an
    EARLIER session is silently skipped rather than re-stamped into the new
    one (the UNIQUE key has no session column, by design — a transcript may
    only ever be reserved once per participant). Callers must therefore
    exclude already-reserved slugs when picking, or the new session comes out
    silently short; assignment.py does this via db.reserved_slugs().

    updated_at is stamped now — this is what makes the reservation
    immediately count as "claimed" in coverage_counts' staleness check.
    Without it, a fresh reservation with a NULL updated_at would look
    abandoned to a concurrently-arriving participant's coverage read and get
    double-assigned."""
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
    """Update the verdict columns of an existing annotation row.

    `verdict_specific` is the hybrid-only per-game whole-game answer(s), passed
    as a JSON string (or None); universal-condition saves leave it NULL.

    Returns True if a matching row existed (turns submitted first), else False.
    """
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


print(f"🗄️  DB config: host={DB_HOST or 'MISSING'} dbname={DB_NAME or 'MISSING'} "
      f"user={DB_USER or 'MISSING'} sslmode={DB_SSLMODE}")
_require_db_config()
init_db()

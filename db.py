"""PostgreSQL persistence for annotation results, keyed by
(game_slug, annotator_id, condition). A fresh connection is opened per
operation and closed on exit, since Gradio runs handlers across threads."""

import contextlib
import json
import os
from datetime import datetime

import psycopg2
from psycopg2 import errors as pg_errors
from dotenv import load_dotenv

# Silently does nothing if .env doesn't exist, e.g. when vars are set some other way.
load_dotenv()

DB_HOST = os.environ.get("DB_HOST")
DB_NAME = os.environ.get("DB_NAME")
DB_USER = os.environ.get("DB_USER")
DB_PASSWORD = os.environ.get("DB_PASSWORD")
DB_PORT = os.environ.get("DB_PORT", "5432")
DB_SSLMODE = os.environ.get("DB_SSLMODE", "require")
DB_GSSENCMODE = os.environ.get("DB_GSSENCMODE", "disable")


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


# The schema lives in postgres_schema.sql and is read from disk, not duplicated
# here. The two used to be maintained in parallel by hand, which meant a column
# added to one silently did not exist in the other.
_SCHEMA_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                            "postgres_schema.sql")

with open(_SCHEMA_PATH) as _fh:
    _SCHEMA = _fh.read()

_TABLES = ("annotations", "turn_ratings", "consents", "practice_completions",
           "question_sets")

# Columns added after the first deployment. CREATE TABLE IF NOT EXISTS is a
# no-op on an existing table, so a database created before these were added
# still satisfies the table check while missing every one of them — which is
# exactly how dimensions would end up silently NULL. Verified explicitly.
_REQUIRED_COLUMNS = {
    "annotations": ("model_id", "domain", "game", "experiment", "instance",
                    "batch_id", "template_id", "question_set_hash"),
    "question_sets": ("question_set_hash", "captured_at", "spec"),
}


def _open():
    return psycopg2.connect(
        host=DB_HOST,
        port=DB_PORT,
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
    """Verify tables AND the columns added since first deployment.

    The app role lacks CREATE, so init_db() falls through to here. Checking only
    table names would pass happily against a table missing every new column.
    """
    with _connect() as conn:
        cur = conn.cursor()
        cur.execute(
            "SELECT " + ", ".join(f"to_regclass('public.{t}')" for t in _TABLES)
        )
        row = cur.fetchone()
        missing = [name for name, present in zip(_TABLES, row) if present is None]
        if missing:
            raise RuntimeError(
                f"Postgres schema is missing table(s) {missing}, and this DB user "
                "lacks CREATE privilege to create them itself. Run "
                "postgres_schema.sql as an admin role against breezy/study first, "
                "then restart the app."
            )
        cur.execute(
            """
            SELECT table_name, column_name FROM information_schema.columns
            WHERE table_schema = 'public' AND table_name = ANY(%s)
            """,
            ([t for t in _REQUIRED_COLUMNS],),
        )
        have = {}
        for table, column in cur.fetchall():
            have.setdefault(table, set()).add(column)

    stale = {t: sorted(set(cols) - have.get(t, set()))
             for t, cols in _REQUIRED_COLUMNS.items()}
    stale = {t: c for t, c in stale.items() if c}
    if stale:
        raise RuntimeError(
            f"Postgres schema is out of date — missing column(s) {stale}. "
            "Re-run postgres_schema.sql as an admin role (it is idempotent), "
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


def _touch_session(cur, annotation_id, annotator_id, condition, now):
    """Refresh the claim lease on every unfinished row of the same sitting.

    The lease is per BATCH, not per transcript: working on one game means the
    participant hasn't abandoned the others, but reserve_games stamps
    updated_at once at claim time, so without this a slow-but-active
    participant's unopened games would go stale under them (see
    assignment.STALE_AFTER_HOURS and coverage_counts' stale_before)."""
    cur.execute(
        "UPDATE annotations SET updated_at=%s "
        "WHERE annotator_id=%s AND condition=%s AND verdict_at IS NULL "
        "  AND COALESCE(session_index,1) = "
        "      (SELECT COALESCE(session_index,1) FROM annotations WHERE id=%s)",
        (now, annotator_id, condition, annotation_id),
    )


# Study dimensions carried on every annotation row. db.py deliberately does not
# import study_set — it has no study knowledge, and study_set -> annotation -> db
# would be a cycle. Callers pass a plain dict.
_DIMS = ("model_id", "domain", "game", "experiment", "instance")


def save_turns(game_slug, meta, source_path, has_reasoning, annotator_id, condition,
               turns_out, started_at=None, session_day=None, session_started_at=None,
               dims=None, question_set_hash=None, question_set_spec=None):
    """Upsert the annotation row and replace its turn ratings. Returns annotation id.

    `dims` carries the five transcript dimensions. batch_id is deliberately NOT
    accepted here: only reserve_games knows it, and a re-submission through the
    debug link must never clear a reservation's batch.
    """
    now = datetime.now().isoformat()
    annotator_id = annotator_id or ""
    condition = condition or ""
    dims = dims or {}
    dim_values = [dims.get(k) for k in _DIMS]
    with _connect() as conn:
        cur = conn.cursor()
        if question_set_hash and question_set_spec:
            cur.execute(
                """
                INSERT INTO question_sets
                    (question_set_hash, captured_at, game, condition, spec)
                VALUES (%s, %s, %s, %s, %s)
                ON CONFLICT (question_set_hash) DO NOTHING
                """,
                (question_set_hash, now, dims.get("game"), condition, question_set_spec),
            )
        cur.execute(
            """
            INSERT INTO annotations
                (game_slug, game_id, game_name, source_path, has_reasoning,
                 annotator_id, condition, session_day, session_started_at,
                 started_at, annotated_at, updated_at,
                 model_id, domain, game, experiment, instance, question_set_hash)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                    %s, %s, %s, %s, %s, %s)
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
                updated_at=excluded.updated_at,
                -- COALESCE so a transcript missing from the manifest leaves any
                -- previously-good dimension alone instead of nulling it.
                model_id=COALESCE(excluded.model_id, annotations.model_id),
                domain=COALESCE(excluded.domain, annotations.domain),
                game=COALESCE(excluded.game, annotations.game),
                experiment=COALESCE(excluded.experiment, annotations.experiment),
                instance=COALESCE(excluded.instance, annotations.instance),
                question_set_hash=COALESCE(excluded.question_set_hash,
                                           annotations.question_set_hash)
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
                *dim_values,
                question_set_hash,
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
        _touch_session(cur, annotation_id, annotator_id, condition, now)
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


def has_completed_practice(annotator_id):
    """Whether this identity has already been through the practice round —
    the gate that stops it being shown twice. Same shape as has_consented:
    an empty annotator_id can never have a record, so no DB hit."""
    if not annotator_id:
        return False
    with _connect() as conn:
        cur = conn.cursor()
        cur.execute(
            "SELECT 1 FROM practice_completions WHERE annotator_id=%s",
            (annotator_id,),
        )
        row = cur.fetchone()
    return row is not None


def record_practice(annotator_id):
    """Record that `annotator_id` is done with the practice round, once.
    Skipping counts — someone who deliberately skipped shouldn't be shown it
    again next session. A no-op for an empty annotator_id."""
    if not annotator_id:
        return
    now = datetime.now().isoformat()
    with _connect() as conn:
        cur = conn.cursor()
        cur.execute(
            "INSERT INTO practice_completions (annotator_id, completed_at) "
            "VALUES (%s, %s) ON CONFLICT(annotator_id) DO NOTHING",
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


def reserve_games(annotator_id, condition, game_slugs, session_index=1, conn=None,
                  batch_id=None, dims_by_slug=None):
    """Claim `game_slugs` for `annotator_id` under `condition` as placeholder
    rows; save_turns fills in the rest on first submit. A slug already
    reserved in an earlier session is silently skipped, so callers must
    exclude already-reserved slugs when picking (db.reserved_slugs()).

    Dimensions are stamped HERE as well as in save_turns, because placeholder
    rows appear in the export too — without them, "which batches are still
    open" is unanswerable. This is the only path that knows batch_id.
    """
    game_slugs = list(game_slugs)
    if not game_slugs:
        return
    now = datetime.now().isoformat()
    dims_by_slug = dims_by_slug or {}
    rows = []
    for slug in game_slugs:
        d = dims_by_slug.get(slug) or {}
        rows.append((slug, annotator_id or "", condition, session_index, now,
                     *[d.get(k) for k in _DIMS],
                     batch_id or d.get("batch_id"), d.get("template_id")))
    sql = ("INSERT INTO annotations "
           "    (game_slug, annotator_id, condition, session_index, updated_at, "
           "     model_id, domain, game, experiment, instance, batch_id, template_id) "
           "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s) "
           "ON CONFLICT(game_slug, annotator_id, condition) DO NOTHING")
    if conn is not None:
        cur = conn.cursor()
        cur.executemany(sql, rows)
        return
    with _connect() as c:
        cur = c.cursor()
        cur.executemany(sql, rows)


_PRUNE_SQL = """
DELETE FROM annotations a
WHERE a.annotator_id=%s AND a.condition=%s
  AND COALESCE(a.session_index,1)=%s
  AND a.verdict_at IS NULL
  AND a.started_at IS NULL
  AND NOT EXISTS (SELECT 1 FROM turn_ratings tr WHERE tr.annotation_id=a.id)
  AND (SELECT COUNT(DISTINCT o.annotator_id) FROM annotations o
       WHERE o.game_slug=a.game_slug AND o.condition=a.condition
         AND o.annotator_id <> a.annotator_id
         AND o.verdict_at IS NOT NULL) >= %s
"""


def prune_over_covered(annotator_id, condition, session_index, coverage_target,
                       conn=None):
    """Release this participant's UNTOUCHED reservations in `session_index`
    whose transcript has since been COMPLETED by `coverage_target` other
    annotators. Returns how many were released.

    Needed because a resumed sitting is otherwise handed back exactly as
    reserved: someone who abandons a batch, lets the claim expire, and returns
    after three other people finished those transcripts would put a fourth
    rating on each of them.

    Two deliberate restrictions:
      - Only rows with no saved work (no started_at, no turn_ratings) go.
        Partial work is never destroyed, and a transcript they've already
        started stays theirs to finish even if that makes it a fourth rating.
      - Only *completed* rows of other annotators count toward the target. A
        transcript held by three live claims isn't finished with — those
        claims can still expire.

    Call inside db.write_transaction() so no one can claim between the count
    and the delete."""
    params = (annotator_id or "", condition, session_index, coverage_target)
    if conn is not None:
        cur = conn.cursor()
        cur.execute(_PRUNE_SQL, params)
        return cur.rowcount
    with _connect() as c:
        cur = c.cursor()
        cur.execute(_PRUNE_SQL, params)
        return cur.rowcount


class QuestionSetChanged(RuntimeError):
    """The question set moved between saving turns and saving the verdict."""


def save_verdict(game_slug, annotator_id, condition, coherence, overall, comment,
                 verdict_specific=None, question_set_hash=None):
    """Update the verdict columns of an existing annotation row. Returns True
    if a matching row existed (turns submitted first), else False.

    If `question_set_hash` is given and differs from the one stored when the
    turns were saved, the questions changed underneath a sitting in progress —
    a redeploy mid-session. Raise rather than write, because the turn answers
    and the verdict would otherwise describe two different question sets in the
    same row. The participant redoes one transcript; the alternative is a row
    that is quietly wrong forever.
    """
    now = datetime.now().isoformat()
    annotator_id = annotator_id or ""
    condition = condition or ""
    with _connect() as conn:
        cur = conn.cursor()
        if question_set_hash:
            cur.execute(
                "SELECT question_set_hash FROM annotations "
                "WHERE game_slug=%s AND annotator_id=%s AND condition=%s",
                (game_slug, annotator_id, condition),
            )
            row = cur.fetchone()
            if row and row[0] and row[0] != question_set_hash:
                raise QuestionSetChanged(
                    f"{game_slug}: turns saved under {row[0]}, "
                    f"verdict under {question_set_hash}"
                )
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
            RETURNING id
            """,
            (coherence, overall, comment, now, verdict_specific, now,
             game_slug, annotator_id, condition),
        )
        row = cur.fetchone()
        ok = row is not None
        if ok:
            # Finishing a game is activity too — keep the rest of the sitting
            # leased so it can't be claimed out from under them.
            _touch_session(cur, row[0], annotator_id, condition, now)
    return ok


# Connecting at import time means `import annotation` (which imports this) needs a
# live database — that makes every file-only test, and the export's own decoding,
# impossible to run off the VM. SKIP_DB_INIT=1 opts out; app.py always initialises.
if os.environ.get("SKIP_DB_INIT") != "1":
    _require_db_config()
    init_db()


def startup():
    """Explicit init for callers that set SKIP_DB_INIT but do need the database."""
    _require_db_config()
    init_db()

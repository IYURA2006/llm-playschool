-- Schema for the annotation app's PostgreSQL database.
--
-- This file is the SINGLE definition: db.py reads it at import and executes it,
-- so there is no second copy in Python to drift out of sync. It is idempotent —
-- safe to re-run against an existing database.
--
-- Grants live in postgres_grants.sql and must be run separately by an admin;
-- the app's own role cannot execute them.
--
--   psql "host=breezy dbname=study sslmode=require gssencmode=disable" \
--        -f postgres_schema.sql
--
-- NOTE on adding columns: CREATE TABLE IF NOT EXISTS does nothing to a table
-- that already exists, so every new column also needs an ALTER below. Without
-- that, an existing database keeps the old shape and the first symptom is
-- either a runtime UndefinedColumn or, worse, silently missing data.

CREATE TABLE IF NOT EXISTS annotations (
    id INTEGER GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    game_slug TEXT NOT NULL,
    game_id INTEGER,
    game_name TEXT,
    source_path TEXT,
    has_reasoning BOOLEAN,
    annotator_id TEXT NOT NULL DEFAULT '',
    condition TEXT,
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

-- Study dimensions, stamped at collection time rather than derived at export.
-- All nullable on purpose: a gap in study_manifest.csv must not hard-fail a
-- live participant mid-sitting. A NULL plus a loud --check is the better
-- failure mode. batch_id additionally stays NULL until assignment.py becomes
-- batch-aware.
ALTER TABLE annotations ADD COLUMN IF NOT EXISTS model_id TEXT;
ALTER TABLE annotations ADD COLUMN IF NOT EXISTS domain TEXT;
ALTER TABLE annotations ADD COLUMN IF NOT EXISTS game TEXT;
ALTER TABLE annotations ADD COLUMN IF NOT EXISTS experiment TEXT;
ALTER TABLE annotations ADD COLUMN IF NOT EXISTS instance TEXT;
ALTER TABLE annotations ADD COLUMN IF NOT EXISTS batch_id TEXT;
-- The template a batch instantiates. Every model's version of a template holds
-- the same instances, so assignment excludes repeats by TEMPLATE, not batch:
-- handing an annotator REF-1 for a second model would re-show transcripts of
-- games they have already rated and break the independence of the 3 ratings.
ALTER TABLE annotations ADD COLUMN IF NOT EXISTS template_id TEXT;
-- Fingerprint of the exact question set this row was collected under; see
-- annotation.question_spec(). Lets the export decode old rows against the
-- questions that were actually shown, not whatever the code says today.
ALTER TABLE annotations ADD COLUMN IF NOT EXISTS question_set_hash TEXT;

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

-- The practice round is unpaid and never recorded as annotation data, so
-- "have they done it?" can't be inferred from the annotations table — it
-- needs its own record, or a page reload replays it.
CREATE TABLE IF NOT EXISTS practice_completions (
    id INTEGER GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    annotator_id TEXT NOT NULL UNIQUE,
    completed_at TIMESTAMP NOT NULL
);

-- One row per distinct question set seen during collection. Storing the SPEC,
-- not just its hash, is what lets a later export decode rows whose questions
-- have since changed — correctly, rather than merely refusing them. Expected
-- size is a handful of rows for an entire study.
CREATE TABLE IF NOT EXISTS question_sets (
    question_set_hash TEXT PRIMARY KEY,
    captured_at TIMESTAMP NOT NULL,
    game TEXT,
    condition TEXT,
    spec TEXT NOT NULL
);

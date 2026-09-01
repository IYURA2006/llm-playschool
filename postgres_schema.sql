-- Schema for the annotation app's PostgreSQL database.
--
-- The only definition: db.py reads and runs this file at import, so there is no
-- second copy to drift. Safe to re-run against an existing database.
--
-- Grants live in postgres_grants.sql and must be run separately by an admin;
-- the app's own role cannot execute them.
--
--   psql "host=breezy dbname=study sslmode=require gssencmode=disable" \
--        -f postgres_schema.sql
--
-- Adding a column: CREATE TABLE IF NOT EXISTS does nothing to a table that
-- already exists, so every new column also needs an ALTER below. Without one,
-- an older database keeps the old shape and quietly loses that data.

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

-- Stamped when the data is collected, not worked out at export. All nullable on
-- purpose: a gap in the manifest must not break a live sitting. A NULL plus a
-- loud --check is the better failure.
ALTER TABLE annotations ADD COLUMN IF NOT EXISTS model_id TEXT;
ALTER TABLE annotations ADD COLUMN IF NOT EXISTS domain TEXT;
ALTER TABLE annotations ADD COLUMN IF NOT EXISTS game TEXT;
ALTER TABLE annotations ADD COLUMN IF NOT EXISTS experiment TEXT;
ALTER TABLE annotations ADD COLUMN IF NOT EXISTS instance TEXT;
ALTER TABLE annotations ADD COLUMN IF NOT EXISTS batch_id TEXT;
-- Every model's version of a template holds the same instances, so assignment
-- excludes repeats by template, not by batch. Giving someone the same template
-- twice would re-show games they already rated and break the 3 ratings'
-- independence.
ALTER TABLE annotations ADD COLUMN IF NOT EXISTS template_id TEXT;
-- Fingerprint of the question set this row was collected under, so the export
-- can decode old rows against the questions really shown at the time.
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

-- The practice round is unpaid and never stored as annotation data, so it needs
-- its own record. Without one, a page reload replays it.
CREATE TABLE IF NOT EXISTS practice_completions (
    id INTEGER GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    annotator_id TEXT NOT NULL UNIQUE,
    completed_at TIMESTAMP NOT NULL
);

-- One row per question set seen during collection. Storing the whole spec, not
-- just its hash, lets a later export decode rows whose questions have since
-- changed. A handful of rows for a whole study.
CREATE TABLE IF NOT EXISTS question_sets (
    question_set_hash TEXT PRIMARY KEY,
    captured_at TIMESTAMP NOT NULL,
    game TEXT,
    condition TEXT,
    spec TEXT NOT NULL
);

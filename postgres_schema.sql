-- One-time manual setup for the annotation app's PostgreSQL schema.
--
-- Run as an admin/owner role (NOT studyuser — studyuser only gets the DML
-- grants below, not CREATE on schema public):
--
--   psql "host=breezy dbname=study sslmode=require gssencmode=disable" -f postgres_schema.sql
--
-- This creates the schema empty — no data import, matching the app's own
-- db.py:_SCHEMA (kept in sync manually; if you change one, change the other).

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

-- Grant the app's service account DML access to everything, including
-- tables created by future migrations.
GRANT USAGE ON SCHEMA public TO studyuser;
GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO studyuser;
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO studyuser;
ALTER DEFAULT PRIVILEGES IN SCHEMA public
  GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO studyuser;

-- Run ONCE by an admin/owner role, separately from postgres_schema.sql.
-- Split out because the app's own role (studyuser) cannot execute GRANT, so
-- keeping these in the schema file made that file unrunnable by the app.
--
--   psql "host=breezy dbname=study sslmode=require gssencmode=disable" \
--        -f postgres_grants.sql

GRANT USAGE ON SCHEMA public TO studyuser;
GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO studyuser;
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO studyuser;
ALTER DEFAULT PRIVILEGES IN SCHEMA public
  GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO studyuser;

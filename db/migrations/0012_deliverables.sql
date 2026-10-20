-- Incremental migration for generic-task deliverables (docs/GENERAL_TASKS_DESIGN.md, step B).
-- Prior migrations remain immutable.
--
-- * DELIVERABLE artifacts are the files a member downloads (translation.docx, report.md,
--   chart-1.png, ...). Several may belong to one attempt, so each carries its file name.
--   Names are plain ASCII file names; the server never derives paths from them.
BEGIN;
ALTER TABLE artifacts DROP CONSTRAINT artifacts_kind_check;
ALTER TABLE artifacts ADD CONSTRAINT artifacts_kind_check CHECK (kind IN
  ('PATCH','TEST_REPORT','LOG','SUMMARY','EXECUTION_LOG','VERIFICATION_REPORT','DELIVERABLE'));
ALTER TABLE artifacts ADD COLUMN name text
  CHECK (name IS NULL OR name ~ '^[A-Za-z0-9][A-Za-z0-9._-]{0,99}$');
ALTER TABLE artifacts ADD CONSTRAINT deliverable_has_name CHECK (
  kind <> 'DELIVERABLE' OR name IS NOT NULL);
-- Document kinds have no repository, hence no base commit to verify against.
ALTER TABLE verification_results ALTER COLUMN base_commit_sha DROP NOT NULL;
COMMIT;

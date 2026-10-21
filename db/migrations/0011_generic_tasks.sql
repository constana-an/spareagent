-- Incremental generic-task migration (docs/GENERAL_TASKS_DESIGN.md, step A). Prior
-- migrations remain immutable.
--
-- * task_kinds: the catalogue of task kinds and their opening status. Only OPEN kinds may
--   be dispatched LIVE (checked again here by trigger); a DISABLED kind is never dispatched.
-- * tasks: repository fields become optional and are required exactly for kinds that work
--   on a repository; `spec` holds the deliverable, parameters and limits.
-- * task_uploads: files a member uploads before submitting; linked to at most one task.
BEGIN;

CREATE TABLE task_kinds (
  kind text PRIMARY KEY CHECK (kind ~ '^[A-Z][A-Z_]{2,39}$'),
  category text NOT NULL CHECK (category IN
    ('WRITING','INFORMATION','RESEARCH','DATA','CODE','FILE_TOOLS')),
  needs_repository boolean NOT NULL,
  status text NOT NULL DEFAULT 'DEVELOPING' CHECK (status IN
    ('DEVELOPING','SIMULATED_OK','LIVE_ACCEPTED','OPEN','DISABLED')),
  acceptance_report text,
  updated_at timestamptz NOT NULL DEFAULT now(),
  updated_by uuid REFERENCES users(id),
  CONSTRAINT open_needs_report CHECK (
    status NOT IN ('LIVE_ACCEPTED','OPEN') OR acceptance_report IS NOT NULL)
);

-- Legacy code kinds keep the behaviour they had before this migration (they were already
-- dispatchable and remain limited by each authorization's task_types scope).
INSERT INTO task_kinds (kind, category, needs_repository, status, acceptance_report) VALUES
  ('CODE_REVIEW', 'CODE', true, 'OPEN', 'reports/week6_two_device_pilot.json'),
  ('CODE_FIX', 'CODE', true, 'OPEN', 'legacy: dispatchable before 0011'),
  ('WRITE_TESTS', 'CODE', true, 'OPEN', 'legacy: dispatchable before 0011'),
  ('REFACTOR', 'CODE', true, 'OPEN', 'legacy: dispatchable before 0011'),
  ('CODE_CHANGE', 'CODE', true, 'DEVELOPING', NULL),
  ('TRANSLATE_DOCUMENT', 'WRITING', false, 'DEVELOPING', NULL),
  ('ANALYZE_SPREADSHEET', 'DATA', false, 'DEVELOPING', NULL);

ALTER TABLE tasks DROP CONSTRAINT tasks_task_type_check;
ALTER TABLE tasks ADD CONSTRAINT tasks_task_kind_fk
  FOREIGN KEY (task_type) REFERENCES task_kinds(kind);
ALTER TABLE tasks ALTER COLUMN repository_url DROP NOT NULL;
ALTER TABLE tasks ALTER COLUMN commit_sha DROP NOT NULL;
ALTER TABLE tasks ADD CONSTRAINT repository_fields_together CHECK (
  (repository_url IS NULL) = (commit_sha IS NULL));
ALTER TABLE tasks ADD COLUMN spec jsonb NOT NULL DEFAULT '{}'::jsonb;
ALTER TABLE tasks ADD COLUMN kind_version int NOT NULL DEFAULT 1 CHECK (kind_version >= 1);

CREATE FUNCTION require_kind_repository() RETURNS trigger AS $$
DECLARE needs boolean;
BEGIN
  SELECT k.needs_repository INTO needs FROM task_kinds k WHERE k.kind = NEW.task_type;
  IF needs IS DISTINCT FROM (NEW.repository_url IS NOT NULL) THEN
    RAISE EXCEPTION 'task kind % % a repository', NEW.task_type,
      CASE WHEN needs THEN 'requires' ELSE 'must not have' END
      USING ERRCODE = 'check_violation';
  END IF;
  RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER trg_kind_repository
  BEFORE INSERT OR UPDATE OF task_type, repository_url ON tasks
  FOR EACH ROW EXECUTE FUNCTION require_kind_repository();

-- A LIVE allow also needs the task's kind to be OPEN at that moment.
CREATE FUNCTION require_open_kind_for_live() RETURNS trigger AS $$
BEGIN
  IF NEW.decision = 'ALLOW' AND NEW.execution_mode = 'LIVE' AND NOT EXISTS (
       SELECT 1 FROM tasks t JOIN task_kinds k ON k.kind = t.task_type
       WHERE t.id = NEW.task_id AND k.status = 'OPEN')
  THEN
    RAISE EXCEPTION 'LIVE dispatch requires an OPEN task kind'
      USING ERRCODE = 'check_violation';
  END IF;
  RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER trg_open_kind_live_dispatch
  BEFORE INSERT ON experimental_dispatch_audit
  FOR EACH ROW EXECUTE FUNCTION require_open_kind_for_live();

CREATE TABLE task_uploads (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  owner_id uuid NOT NULL REFERENCES users(id),
  storage_key text NOT NULL,
  sha256 char(64) NOT NULL CHECK (sha256 ~ '^[0-9a-f]{64}$'),
  size_bytes bigint NOT NULL CHECK (size_bytes BETWEEN 1 AND 20971520),
  filename text NOT NULL CHECK (char_length(filename) BETWEEN 1 AND 200),
  extension text NOT NULL CHECK (extension ~ '^[a-z0-9]{1,8}$'),
  media_type text NOT NULL,
  created_at timestamptz NOT NULL DEFAULT now(),
  expires_at timestamptz NOT NULL,
  task_id uuid REFERENCES tasks(id),
  position int CHECK (position BETWEEN 0 AND 4),
  CONSTRAINT linked_has_position CHECK ((task_id IS NULL) = (position IS NULL)),
  CONSTRAINT one_position_per_task UNIQUE (task_id, position)
);
CREATE INDEX idx_task_uploads_task ON task_uploads(task_id) WHERE task_id IS NOT NULL;
CREATE INDEX idx_task_uploads_unlinked ON task_uploads(expires_at) WHERE task_id IS NULL;
COMMIT;

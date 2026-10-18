-- SpareAgent migration 0005: sandboxed code execution + independent verification (Week 3).
BEGIN;

-- Decided by the API at task creation (never by the client or a worker).
ALTER TABLE tasks ADD COLUMN execution_profile text NOT NULL DEFAULT 'MOCK'
  CHECK (execution_profile IN ('MOCK','CODE'));

-- New artifact kinds; provenance; publication gate (worker artifacts of CODE tasks stay
-- unpublished until the platform verifier passes them).
ALTER TABLE artifacts DROP CONSTRAINT artifacts_kind_check;
ALTER TABLE artifacts ADD CONSTRAINT artifacts_kind_check
  CHECK (kind IN ('PATCH','TEST_REPORT','LOG','SUMMARY','EXECUTION_LOG','VERIFICATION_REPORT'));
ALTER TABLE artifacts ADD COLUMN source text NOT NULL DEFAULT 'WORKER'
  CHECK (source IN ('WORKER','PLATFORM'));
ALTER TABLE artifacts ADD COLUMN published boolean NOT NULL DEFAULT true;
ALTER TABLE artifacts ADD CONSTRAINT verification_report_from_platform
  CHECK (kind <> 'VERIFICATION_REPORT' OR source = 'PLATFORM');

-- Platform verification outcome, recorded separately from anything the worker reported.
CREATE TABLE verification_results (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  task_id uuid NOT NULL REFERENCES tasks(id),
  attempt_id uuid NOT NULL UNIQUE REFERENCES task_attempts(id),
  status text NOT NULL CHECK (status IN ('RUNNING','PASSED','FAILED','ERROR')),
  reason text,
  base_commit_sha char(40) NOT NULL,
  patch_sha256 char(64),
  worker_reported jsonb NOT NULL DEFAULT '{}'::jsonb,
  verified_result jsonb NOT NULL DEFAULT '{}'::jsonb,
  verifier_id text NOT NULL,
  error_count int NOT NULL DEFAULT 0 CHECK (error_count >= 0),
  claimed_until timestamptz,
  started_at timestamptz NOT NULL DEFAULT now(),
  completed_at timestamptz
);
CREATE INDEX idx_verification_pending ON verification_results(status, claimed_until)
  WHERE status IN ('RUNNING','ERROR');

-- Invariant enforced by the database, independent of application version (a stale or
-- compromised process cannot bypass it): a CODE task can only SUCCEED, and its worker
-- artifacts can only be published, once the platform verifier recorded PASSED.
CREATE FUNCTION require_platform_verification() RETURNS trigger AS $$
BEGIN
  IF TG_TABLE_NAME = 'tasks' THEN
    IF NEW.status = 'SUCCEEDED' AND NEW.execution_profile = 'CODE' AND NOT EXISTS (
         SELECT 1 FROM verification_results v WHERE v.task_id = NEW.id AND v.status = 'PASSED')
    THEN
      RAISE EXCEPTION 'CODE task % cannot succeed without platform verification', NEW.id
        USING ERRCODE = 'check_violation';
    END IF;
  ELSIF NEW.published AND NEW.source = 'WORKER' AND EXISTS (
         SELECT 1 FROM tasks t WHERE t.id = NEW.task_id AND t.execution_profile = 'CODE')
     AND NOT EXISTS (
         SELECT 1 FROM verification_results v WHERE v.attempt_id = NEW.attempt_id
           AND v.status = 'PASSED')
  THEN
    RAISE EXCEPTION 'worker artifact of a CODE task cannot be published unverified'
      USING ERRCODE = 'check_violation';
  END IF;
  RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER trg_code_task_requires_verification
  BEFORE INSERT OR UPDATE OF status ON tasks
  FOR EACH ROW EXECUTE FUNCTION require_platform_verification();
CREATE TRIGGER trg_code_artifact_requires_verification
  BEFORE INSERT OR UPDATE OF published ON artifacts
  FOR EACH ROW EXECUTE FUNCTION require_platform_verification();

COMMIT;

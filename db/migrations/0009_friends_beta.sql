-- Incremental Week 5 migration. Prior migrations remain immutable.
BEGIN;
ALTER TABLE verification_results DROP CONSTRAINT verification_results_status_check;
ALTER TABLE verification_results ADD CONSTRAINT verification_results_status_check
  CHECK (status IN ('RUNNING','PASSED','FAILED','ERROR','UNVERIFIED'));
CREATE TABLE friends_login_invites (
 id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
 user_id uuid NOT NULL REFERENCES users(id),
 token_digest char(64) NOT NULL UNIQUE,
 expires_at timestamptz NOT NULL,
 used_at timestamptz,
 created_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE friends_sessions (
 id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
 user_id uuid NOT NULL REFERENCES users(id),
 token_digest char(64) NOT NULL UNIQUE,
 expires_at timestamptz NOT NULL,
 revoked_at timestamptz,
 created_at timestamptz NOT NULL DEFAULT now()
);
ALTER TABLE subscription_usage_observations
 ADD COLUMN cached_input_tokens bigint CHECK (cached_input_tokens >= 0),
 ADD COLUMN reasoning_output_tokens bigint CHECK (reasoning_output_tokens >= 0);
-- Extend the Week 3 publication invariant to LIVE, including explicit UNVERIFIED.
CREATE OR REPLACE FUNCTION require_platform_verification() RETURNS trigger AS $$
BEGIN
  IF TG_TABLE_NAME = 'tasks' THEN
    IF NEW.status = 'SUCCEEDED' AND (
       (NEW.execution_profile = 'CODE' AND NOT EXISTS (
          SELECT 1 FROM verification_results v WHERE v.task_id = NEW.id AND v.status = 'PASSED'))
       OR (NEW.execution_profile = 'CODEX_LIVE' AND NOT EXISTS (
          SELECT 1 FROM verification_results v WHERE v.task_id = NEW.id
            AND v.status IN ('PASSED','UNVERIFIED'))))
    THEN
      RAISE EXCEPTION 'task cannot succeed without platform verification' USING ERRCODE = 'check_violation';
    END IF;
  ELSIF NEW.published AND NEW.source = 'WORKER' AND EXISTS (
      SELECT 1 FROM tasks t WHERE t.id = NEW.task_id AND (
        (t.execution_profile = 'CODE' AND NOT EXISTS (
          SELECT 1 FROM verification_results v WHERE v.attempt_id = NEW.attempt_id
            AND v.status = 'PASSED')) OR
        (t.execution_profile = 'CODEX_LIVE' AND NOT EXISTS (
          SELECT 1 FROM verification_results v WHERE v.attempt_id = NEW.attempt_id
            AND v.status IN ('PASSED','UNVERIFIED')))))
  THEN
    RAISE EXCEPTION 'worker artifact cannot be published unverified'
      USING ERRCODE = 'check_violation';
  END IF;
  RETURN NEW;
END;
$$ LANGUAGE plpgsql;
COMMIT;

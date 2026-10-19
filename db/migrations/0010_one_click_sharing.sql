-- Incremental one-click sharing migration. Prior migrations remain immutable.
--
-- * A provider can own a self-serve group (owner_id) that others join with a rotating
--   invite code; only the code's SHA-256 digest is stored.
-- * An owner can grant standing approval for tasks from their own group's active members
--   (auto_approve_members). Such approvals are recorded with their own consent source so
--   they stay distinguishable from per-task OWNER_APPROVAL in every audit.
BEGIN;
ALTER TABLE experimental_groups
  ADD COLUMN owner_id uuid REFERENCES users(id),
  ADD COLUMN invite_code_digest char(64) UNIQUE,
  ADD COLUMN invite_code_rotated_at timestamptz;
ALTER TABLE experimental_groups ADD CONSTRAINT invite_code_self_serve_only CHECK (
  invite_code_digest IS NULL OR owner_id IS NOT NULL);

ALTER TABLE subscription_sharing_preferences
  ADD COLUMN auto_approve_members boolean NOT NULL DEFAULT false;

ALTER TABLE experimental_task_consents
  DROP CONSTRAINT experimental_task_consents_consent_source_check;
ALTER TABLE experimental_task_consents ADD CONSTRAINT experimental_task_consents_consent_source_check
  CHECK (consent_source IN ('OWNER_APPROVAL','ADVANCE_CONSENT','OWNER_STANDING_APPROVAL'));
COMMIT;

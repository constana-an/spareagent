-- SpareAgent migration 0008: Milestone D - gated LIVE experimental execution (Week 4).
--
-- Scope confirmed in writing by OpenAI Support (ticket 10766658, 2026-10-18): official
-- Codex CLI (`codex exec`) signed in with each participant's own ChatGPT account on their
-- own computer; a relay server that never handles credentials; at most 5 participants;
-- purchased Codex credits never consumed by another participant's task; review after
-- 6 months. Every LIVE allow must point at a verified authorization that matches this.
BEGIN;

-- Authorization records name the confirmed technical route and participant cap.
ALTER TABLE experimental_authorizations
  ADD COLUMN integration_route text CHECK (integration_route IN ('CODEX_CLI_EXEC')),
  ADD COLUMN max_participants int CHECK (max_participants BETWEEN 2 AND 20);
ALTER TABLE experimental_authorizations ADD CONSTRAINT route_covered_names_route CHECK (
  NOT integration_route_covered OR (integration_route IS NOT NULL AND max_participants IS NOT NULL));

-- Owner attestations, recorded by the owner (never inferred):
--  * owner_check_passed_at: the owner ran `spareagent-worker codex-check` on this device and
--    it reported OWNER_ONLY_SUBSCRIPTION_RUN_OK (ChatGPT login, no API key).
--  * no_purchased_credits_at: the owner confirms the account holds no purchased Codex
--    credits and has no automatic credit top-up, so a shared task can only consume
--    included plan usage. SpareAgent cannot observe credit balances.
ALTER TABLE subscription_connections
  ADD COLUMN owner_check_passed_at timestamptz,
  ADD COLUMN no_purchased_credits_at timestamptz;

-- LIVE experimental tasks run the owner's Codex CLI ('CODEX_LIVE'); simulated ones stay MOCK.
ALTER TABLE tasks DROP CONSTRAINT tasks_execution_profile_check;
ALTER TABLE tasks ADD CONSTRAINT tasks_execution_profile_check
  CHECK (execution_profile IN ('MOCK','CODE','CODEX_LIVE'));
ALTER TABLE tasks DROP CONSTRAINT experimental_tasks_simulated_only;
ALTER TABLE tasks ADD CONSTRAINT experimental_tasks_profile CHECK (
  resource_scope <> 'EXPERIMENTAL_SHARED_SUBSCRIPTION' OR execution_profile IN ('MOCK','CODEX_LIVE'));
ALTER TABLE tasks ADD CONSTRAINT codex_live_experimental_only CHECK (
  execution_profile <> 'CODEX_LIVE' OR resource_scope = 'EXPERIMENTAL_SHARED_SUBSCRIPTION');

-- A LIVE allow is no longer impossible, but the database checks it against the record.
ALTER TABLE experimental_dispatch_audit DROP CONSTRAINT live_never_allowed;

CREATE FUNCTION require_authorized_live_dispatch() RETURNS trigger AS $$
BEGIN
  IF NEW.decision = 'ALLOW' AND NEW.execution_mode = 'LIVE' AND NOT EXISTS (
       SELECT 1 FROM experimental_authorizations a
       WHERE a.id = NEW.authorization_id AND a.group_id = NEW.group_id
         AND a.verification_status = 'OPERATOR_VERIFIED' AND a.integration_route_covered
         AND a.integration_route = 'CODEX_CLI_EXEC'
         AND a.revoked_at IS NULL AND a.expires_at > now())
  THEN
    RAISE EXCEPTION 'LIVE dispatch requires a verified, unexpired, route-covered authorization'
      USING ERRCODE = 'check_violation';
  END IF;
  IF NEW.decision = 'ALLOW' AND NEW.execution_mode = 'LIVE' AND NOT EXISTS (
       SELECT 1 FROM subscription_connections c
       WHERE c.id = NEW.connection_id AND c.owner_check_passed_at IS NOT NULL
         AND c.no_purchased_credits_at IS NOT NULL)
  THEN
    RAISE EXCEPTION 'LIVE dispatch requires the owner''s codex-check and credits attestation'
      USING ERRCODE = 'check_violation';
  END IF;
  RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER trg_authorized_live_dispatch
  BEFORE INSERT ON experimental_dispatch_audit
  FOR EACH ROW EXECUTE FUNCTION require_authorized_live_dispatch();

-- D-22 narrowed (not removed): a PERSONAL_SUBSCRIPTION resource may enroll a node only
-- when the token is issued by the resource's own owner, who is an ACTIVE member of an
-- ACTIVE experimental group. Marketplace enrollment of personal resources stays impossible,
-- and personal resources remain non-public (no_personal_public CHECK, unchanged).
CREATE OR REPLACE FUNCTION forbid_personal_enrollment() RETURNS trigger AS $$
BEGIN
  IF EXISTS (SELECT 1 FROM resources r WHERE r.id = NEW.resource_id
             AND r.resource_type = 'PERSONAL_SUBSCRIPTION')
     AND NOT EXISTS (
       SELECT 1 FROM resources r
       JOIN experimental_group_members m ON m.user_id = r.owner_id AND m.status = 'ACTIVE'
       JOIN experimental_groups g ON g.id = m.group_id AND g.status = 'ACTIVE'
       WHERE r.id = NEW.resource_id AND r.owner_id = NEW.issued_by AND r.public_eligible = false)
  THEN
    RAISE EXCEPTION 'PERSONAL_SUBSCRIPTION resources can only self-enroll for an experimental group'
      USING ERRCODE = 'check_violation';
  END IF;
  RETURN NEW;
END;
$$ LANGUAGE plpgsql;

COMMIT;

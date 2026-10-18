-- SpareAgent migration 0007: experimental, invitation-only subscription-sharing scope (Week 4).
--
-- Numbered 0007 (not 0006) so it cannot collide with the separate, unmerged Week 4
-- web/GitHub branch, which uses 0006_web_github. The two migrations are independent.
--
-- Nothing here stores ChatGPT/Codex credentials: there is no column for tokens, cookies,
-- passwords or keys, and authorization records hold only an opaque reference to private
-- correspondence, never its text.
BEGIN;

-- A separate execution scope, never mixed with PUBLIC_MARKETPLACE.
ALTER TABLE tasks DROP CONSTRAINT tasks_resource_scope_check;
ALTER TABLE tasks ADD CONSTRAINT tasks_resource_scope_check CHECK (
  resource_scope IN ('PUBLIC_MARKETPLACE','OWNER_ONLY','EXPERIMENTAL_SHARED_SUBSCRIPTION'));
ALTER TABLE tasks DROP CONSTRAINT tasks_task_type_check;
ALTER TABLE tasks ADD CONSTRAINT tasks_task_type_check CHECK (
  task_type IN ('CODE_FIX','REFACTOR','WRITE_TESTS','CODE_REVIEW'));
-- Live cross-user execution through a personal plan is NOT enabled (WEEK4 blocker B-1):
-- experimental tasks can only ever run the deterministic simulated (MOCK) profile.
-- Lifting this requires a new, reviewed migration.
ALTER TABLE tasks ADD CONSTRAINT experimental_tasks_simulated_only CHECK (
  resource_scope <> 'EXPERIMENTAL_SHARED_SUBSCRIPTION' OR execution_profile = 'MOCK');
-- CODE_REVIEW exists only inside the experimental scope.
ALTER TABLE tasks ADD CONSTRAINT code_review_experimental_only CHECK (
  task_type <> 'CODE_REVIEW' OR resource_scope = 'EXPERIMENTAL_SHARED_SUBSCRIPTION');

CREATE TABLE experimental_groups (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  name text NOT NULL CHECK (char_length(name) BETWEEN 1 AND 100),
  created_by uuid NOT NULL REFERENCES users(id),
  status text NOT NULL DEFAULT 'ACTIVE' CHECK (status IN ('ACTIVE','DISABLED')),
  -- Operator switch; defaults off. Cross-user dispatch also needs a verified authorization.
  sharing_enabled boolean NOT NULL DEFAULT false,
  max_members int NOT NULL DEFAULT 5 CHECK (max_members BETWEEN 2 AND 20),
  max_concurrent_tasks int NOT NULL DEFAULT 1 CHECK (max_concurrent_tasks BETWEEN 1 AND 4),
  max_tasks_per_day int NOT NULL DEFAULT 5 CHECK (max_tasks_per_day BETWEEN 1 AND 50),
  max_task_duration_seconds int NOT NULL DEFAULT 900
    CHECK (max_task_duration_seconds BETWEEN 10 AND 3600),
  allowed_task_types jsonb NOT NULL DEFAULT '["CODE_REVIEW","WRITE_TESTS","CODE_FIX"]'::jsonb,
  created_at timestamptz NOT NULL DEFAULT now(),
  disabled_at timestamptz,
  CONSTRAINT disabled_group_not_sharing CHECK (status = 'ACTIVE' OR sharing_enabled = false)
);

CREATE TABLE experimental_group_members (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  group_id uuid NOT NULL REFERENCES experimental_groups(id),
  user_id uuid NOT NULL REFERENCES users(id),
  status text NOT NULL DEFAULT 'INVITED' CHECK (status IN ('INVITED','ACTIVE','REMOVED')),
  invited_by uuid NOT NULL REFERENCES users(id),
  invited_at timestamptz NOT NULL DEFAULT now(),
  joined_at timestamptz,
  removed_at timestamptz,
  terms_version text,
  acknowledgements jsonb NOT NULL DEFAULT '{}'::jsonb,
  CONSTRAINT uniq_group_member UNIQUE (group_id, user_id),
  CONSTRAINT active_member_consented CHECK (
    status <> 'ACTIVE' OR (joined_at IS NOT NULL AND terms_version IS NOT NULL))
);
CREATE INDEX idx_group_members_user ON experimental_group_members(user_id, status);

CREATE TABLE experimental_authorizations (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  group_id uuid NOT NULL REFERENCES experimental_groups(id),
  provider_name text NOT NULL CHECK (provider_name IN ('openai-codex')),
  authorization_source text NOT NULL
    CHECK (authorization_source IN ('VENDOR_SUPPORT_CORRESPONDENCE','VENDOR_WRITTEN_AGREEMENT')),
  -- Opaque pointer (e.g. a ticket number) to privately held correspondence. Never the text.
  authorization_reference text NOT NULL CHECK (char_length(authorization_reference) <= 200),
  authorization_scope jsonb NOT NULL DEFAULT '{}'::jsonb,
  verification_status text NOT NULL DEFAULT 'UNVERIFIED'
    CHECK (verification_status IN ('UNVERIFIED','OPERATOR_VERIFIED','REJECTED')),
  -- Whether the specific technical route (Codex CLI/app-server, cross-user trigger) was
  -- confirmed as covered. Recorded for the record; live execution stays disabled in code.
  integration_route_covered boolean NOT NULL DEFAULT false,
  verified_by uuid REFERENCES users(id),
  verified_at timestamptz,
  expires_at timestamptz NOT NULL,
  revoked_at timestamptz,
  created_by uuid NOT NULL REFERENCES users(id),
  created_at timestamptz NOT NULL DEFAULT now(),
  CONSTRAINT verified_has_verifier CHECK (
    verification_status = 'UNVERIFIED' OR (verified_by IS NOT NULL AND verified_at IS NOT NULL)),
  CONSTRAINT authorization_expiry_after_creation CHECK (expires_at > created_at)
);
CREATE INDEX idx_authorizations_group ON experimental_authorizations(group_id, created_at DESC);

CREATE TABLE subscription_connections (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  owner_id uuid NOT NULL REFERENCES users(id),
  worker_id uuid NOT NULL UNIQUE REFERENCES worker_nodes(id),
  provider_name text NOT NULL DEFAULT 'openai-codex' CHECK (provider_name IN ('openai-codex')),
  subscription_type text NOT NULL DEFAULT 'UNKNOWN'
    CHECK (subscription_type IN ('PLUS','PRO','UNKNOWN')),
  -- Self-reported by the owner's device after `codex login status`; only ChatGPT logins.
  auth_method text NOT NULL CHECK (auth_method = 'CHATGPT'),
  auth_state text NOT NULL DEFAULT 'VALID' CHECK (auth_state IN ('VALID','EXPIRED','UNKNOWN')),
  device_label text NOT NULL CHECK (char_length(device_label) BETWEEN 1 AND 80),
  status text NOT NULL DEFAULT 'CONNECTED' CHECK (status IN ('CONNECTED','DISCONNECTED')),
  last_auth_check_at timestamptz,
  created_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX idx_subscription_connections_owner ON subscription_connections(owner_id);

CREATE TABLE subscription_sharing_preferences (
  connection_id uuid PRIMARY KEY REFERENCES subscription_connections(id),
  group_id uuid NOT NULL REFERENCES experimental_groups(id),
  sharing_state text NOT NULL DEFAULT 'DISABLED'
    CHECK (sharing_state IN ('DISABLED','ENABLED','PAUSED','REVOKED')),
  consent_terms_version text,
  consent_accepted_at timestamptz,
  max_concurrent_tasks int NOT NULL DEFAULT 1 CHECK (max_concurrent_tasks BETWEEN 1 AND 4),
  max_tasks_per_day int NOT NULL DEFAULT 5 CHECK (max_tasks_per_day BETWEEN 0 AND 50),
  max_task_duration_seconds int NOT NULL DEFAULT 900
    CHECK (max_task_duration_seconds BETWEEN 10 AND 3600),
  allowed_task_types jsonb NOT NULL DEFAULT '["CODE_REVIEW","WRITE_TESTS","CODE_FIX"]'::jsonb,
  require_owner_approval boolean NOT NULL DEFAULT true,
  -- Scoped advance consent: requesters whose tasks need no per-task approval.
  advance_consent_requester_ids jsonb NOT NULL DEFAULT '[]'::jsonb,
  allow_paid_credits boolean NOT NULL DEFAULT false,
  paused_at timestamptz,
  revoked_at timestamptz,
  updated_at timestamptz NOT NULL DEFAULT now(),
  CONSTRAINT never_paid_credits CHECK (allow_paid_credits = false),
  CONSTRAINT enabled_requires_consent CHECK (
    sharing_state <> 'ENABLED' OR consent_accepted_at IS NOT NULL),
  CONSTRAINT revoked_is_final_stamp CHECK (sharing_state <> 'REVOKED' OR revoked_at IS NOT NULL)
);

-- Observations only. No column for remaining tokens/quota: Codex exposes no supported
-- measurement, so capacity is UNKNOWN unless a supported source reports otherwise.
CREATE TABLE subscription_usage_observations (
  id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  connection_id uuid NOT NULL REFERENCES subscription_connections(id),
  task_attempt_id uuid REFERENCES task_attempts(id),
  event text NOT NULL CHECK (event IN
    ('TASK_COMPLETED','TASK_FAILED','RATE_LIMITED','AUTH_EXPIRED','AVAILABILITY')),
  source text NOT NULL CHECK (source IN ('SIMULATED','LOCAL_RUNTIME','OWNER_REPORTED')),
  duration_ms bigint CHECK (duration_ms >= 0),
  input_tokens bigint CHECK (input_tokens >= 0),
  output_tokens bigint CHECK (output_tokens >= 0),
  capacity_state text NOT NULL DEFAULT 'UNKNOWN'
    CHECK (capacity_state IN ('UNKNOWN','AVAILABLE','LIMITED')),
  reset_hint text CHECK (char_length(reset_hint) <= 80),
  observed_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX idx_usage_observations_conn ON subscription_usage_observations(connection_id, observed_at DESC);

-- One row per experimental task: who asked, whose runtime, and the owner's decision.
CREATE TABLE experimental_task_consents (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  task_id uuid NOT NULL UNIQUE REFERENCES tasks(id),
  group_id uuid NOT NULL REFERENCES experimental_groups(id),
  requester_id uuid NOT NULL REFERENCES users(id),
  connection_id uuid NOT NULL REFERENCES subscription_connections(id),
  owner_id uuid NOT NULL REFERENCES users(id),
  status text NOT NULL DEFAULT 'PENDING'
    CHECK (status IN ('PENDING','APPROVED','REJECTED','WITHDRAWN')),
  consent_source text CHECK (consent_source IN ('OWNER_APPROVAL','ADVANCE_CONSENT')),
  decided_at timestamptz,
  created_at timestamptz NOT NULL DEFAULT now(),
  CONSTRAINT decided_has_source CHECK (
    status IN ('PENDING','WITHDRAWN') OR (consent_source IS NOT NULL AND decided_at IS NOT NULL))
);
CREATE INDEX idx_task_consents_owner ON experimental_task_consents(owner_id, status, created_at DESC);
CREATE INDEX idx_task_consents_conn ON experimental_task_consents(connection_id, created_at);

-- Every cross-user scheduling decision. Append-only (trigger below).
CREATE TABLE experimental_dispatch_audit (
  id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  task_id uuid NOT NULL REFERENCES tasks(id),
  group_id uuid NOT NULL REFERENCES experimental_groups(id),
  connection_id uuid REFERENCES subscription_connections(id),
  authorization_id uuid REFERENCES experimental_authorizations(id),
  decision text NOT NULL CHECK (decision IN ('ALLOW','DENY')),
  reasons jsonb NOT NULL DEFAULT '[]'::jsonb,
  execution_mode text NOT NULL CHECK (execution_mode IN ('SIMULATED','LIVE')),
  attempt_id uuid REFERENCES task_attempts(id),
  created_at timestamptz NOT NULL DEFAULT now(),
  CONSTRAINT live_never_allowed CHECK (NOT (decision = 'ALLOW' AND execution_mode = 'LIVE'))
);
CREATE INDEX idx_dispatch_audit_task ON experimental_dispatch_audit(task_id, id DESC);
CREATE INDEX idx_dispatch_audit_group ON experimental_dispatch_audit(group_id, created_at DESC);

CREATE FUNCTION forbid_audit_mutation() RETURNS trigger AS $$
BEGIN
  RAISE EXCEPTION '% is append-only', TG_TABLE_NAME USING ERRCODE = 'insufficient_privilege';
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER trg_dispatch_audit_append_only
  BEFORE UPDATE OR DELETE ON experimental_dispatch_audit
  FOR EACH ROW EXECUTE FUNCTION forbid_audit_mutation();

-- A subscription connection must sit on a PERSONAL_SUBSCRIPTION resource owned by the
-- same user (owner-controlled runtime), and such a resource can never be public.
CREATE FUNCTION require_owner_personal_runtime() RETURNS trigger AS $$
BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM worker_nodes w JOIN resources r ON r.id = w.resource_id
    WHERE w.id = NEW.worker_id AND r.resource_type = 'PERSONAL_SUBSCRIPTION'
      AND r.owner_id = NEW.owner_id AND r.public_eligible = false)
  THEN
    RAISE EXCEPTION 'subscription connection must use the owner''s own personal runtime'
      USING ERRCODE = 'check_violation';
  END IF;
  RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER trg_connection_owner_runtime
  BEFORE INSERT OR UPDATE OF worker_id, owner_id ON subscription_connections
  FOR EACH ROW EXECUTE FUNCTION require_owner_personal_runtime();

COMMIT;

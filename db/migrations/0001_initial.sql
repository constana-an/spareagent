-- SpareAgent migration 0001. Requires PostgreSQL >=15 and pgcrypto.
BEGIN;
CREATE EXTENSION IF NOT EXISTS pgcrypto;

CREATE TABLE users (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  email text NOT NULL UNIQUE,
  role text NOT NULL DEFAULT 'CONSUMER' CHECK (role IN ('CONSUMER','PROVIDER','ADMIN')),
  created_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE resources (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  owner_id uuid NOT NULL REFERENCES users(id),
  resource_type text NOT NULL CHECK (resource_type IN ('PERSONAL_SUBSCRIPTION','MANAGED_API','LICENSED_PROVIDER','SELF_HOSTED')),
  authorization_status text NOT NULL DEFAULT 'PENDING' CHECK (authorization_status IN ('PENDING','APPROVED','REJECTED','REVOKED')),
  public_eligible boolean NOT NULL DEFAULT false,
  license_reference text,
  region text NOT NULL DEFAULT 'us-east-1',
  created_at timestamptz NOT NULL DEFAULT now(),
  CONSTRAINT no_personal_public CHECK (resource_type <> 'PERSONAL_SUBSCRIPTION' OR public_eligible = false),
  CONSTRAINT public_requires_approval CHECK (NOT public_eligible OR authorization_status='APPROVED')
);
CREATE TABLE worker_nodes (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  resource_id uuid NOT NULL REFERENCES resources(id),
  name text NOT NULL,
  status text NOT NULL DEFAULT 'OFFLINE' CHECK (status IN ('OFFLINE','IDLE','RESERVED','RUNNING','DRAINING','ERROR')),
  capabilities jsonb NOT NULL DEFAULT '{}'::jsonb,
  max_concurrency int NOT NULL DEFAULT 1 CHECK (max_concurrency BETWEEN 1 AND 100),
  active_tasks int NOT NULL DEFAULT 0 CHECK (active_tasks >= 0),
  last_heartbeat timestamptz,
  created_at timestamptz NOT NULL DEFAULT now(),
  CONSTRAINT bounded_concurrency CHECK (active_tasks <= max_concurrency)
);
CREATE TABLE tasks (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  consumer_id uuid NOT NULL REFERENCES users(id),
  idempotency_key text NOT NULL,
  request_digest char(64) NOT NULL,
  task_type text NOT NULL CHECK (task_type IN ('CODE_FIX','REFACTOR','WRITE_TESTS')),
  repository_url text NOT NULL,
  commit_sha char(40) NOT NULL,
  prompt text NOT NULL CHECK (char_length(prompt) BETWEEN 1 AND 12000),
  resource_scope text NOT NULL DEFAULT 'PUBLIC_MARKETPLACE' CHECK (resource_scope IN ('PUBLIC_MARKETPLACE','OWNER_ONLY')),
  status text NOT NULL DEFAULT 'CREATED' CHECK (status IN ('CREATED','QUEUED','RESERVED','RUNNING','VERIFYING','RETRY_PENDING','CANCELLING','SUCCEEDED','FAILED','CANCELLED','TIMED_OUT')),
  max_budget_cents bigint NOT NULL CHECK (max_budget_cents BETWEEN 1 AND 10000000),
  max_duration_seconds int NOT NULL CHECK (max_duration_seconds BETWEEN 10 AND 7200),
  max_attempts int NOT NULL DEFAULT 2 CHECK (max_attempts BETWEEN 1 AND 5),
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now(),
  CONSTRAINT uniq_consumer_idempotency UNIQUE(consumer_id,idempotency_key)
);
CREATE INDEX idx_tasks_dispatch ON tasks(status,created_at) WHERE status IN ('QUEUED','RETRY_PENDING');
CREATE INDEX idx_tasks_consumer ON tasks(consumer_id,created_at DESC);
CREATE TABLE task_attempts (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  task_id uuid NOT NULL REFERENCES tasks(id),
  worker_id uuid NOT NULL REFERENCES worker_nodes(id),
  attempt_number int NOT NULL CHECK (attempt_number > 0),
  lease_id uuid NOT NULL DEFAULT gen_random_uuid() UNIQUE,
  status text NOT NULL CHECK (status IN ('RESERVED','RUNNING','VERIFYING','SUCCEEDED','FAILED','EXPIRED','CANCELLED')),
  leased_until timestamptz NOT NULL,
  started_at timestamptz,
  completed_at timestamptz,
  finalization_key text UNIQUE,
  CONSTRAINT uniq_attempt_number UNIQUE(task_id,attempt_number)
);
CREATE INDEX idx_attempt_lease_expiry ON task_attempts(leased_until) WHERE status IN ('RESERVED','RUNNING');
CREATE TABLE execution_events (
  id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  task_id uuid NOT NULL REFERENCES tasks(id),
  attempt_id uuid REFERENCES task_attempts(id),
  seq bigint NOT NULL CHECK (seq > 0),
  event_type text NOT NULL,
  payload jsonb NOT NULL DEFAULT '{}'::jsonb,
  created_at timestamptz NOT NULL DEFAULT now(),
  UNIQUE(task_id,seq)
);
CREATE TABLE artifacts (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  task_id uuid NOT NULL REFERENCES tasks(id),
  attempt_id uuid REFERENCES task_attempts(id),
  kind text NOT NULL CHECK (kind IN ('PATCH','TEST_REPORT','LOG','SUMMARY')),
  storage_key text NOT NULL UNIQUE,
  sha256 char(64) NOT NULL,
  media_type text NOT NULL,
  created_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE usage_records (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  task_attempt_id uuid REFERENCES task_attempts(id),
  gateway_request_id uuid,
  provider_request_id text NOT NULL,
  provider_name text NOT NULL,
  model_name text NOT NULL,
  input_tokens bigint NOT NULL DEFAULT 0 CHECK (input_tokens>=0),
  output_tokens bigint NOT NULL DEFAULT 0 CHECK (output_tokens>=0),
  estimated_cost_cents bigint NOT NULL DEFAULT 0 CHECK (estimated_cost_cents>=0),
  verified_cost_cents bigint CHECK (verified_cost_cents>=0),
  created_at timestamptz NOT NULL DEFAULT now(),
  UNIQUE(provider_name,provider_request_id),
  CHECK ((task_attempt_id IS NOT NULL) <> (gateway_request_id IS NOT NULL))
);
CREATE TABLE gateway_requests (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  consumer_id uuid NOT NULL REFERENCES users(id),
  model_alias text NOT NULL,
  status text NOT NULL CHECK(status IN ('STARTED','SUCCEEDED','FAILED')),
  created_at timestamptz NOT NULL DEFAULT now()
);
ALTER TABLE usage_records ADD CONSTRAINT fk_gateway_request FOREIGN KEY(gateway_request_id) REFERENCES gateway_requests(id);
CREATE TABLE audit_logs (
  id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  actor_id uuid REFERENCES users(id),
  action text NOT NULL,
  target_type text NOT NULL,
  target_id text NOT NULL,
  metadata jsonb NOT NULL DEFAULT '{}'::jsonb,
  created_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX idx_worker_health ON worker_nodes(status,last_heartbeat);
COMMIT;

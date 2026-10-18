-- SpareAgent migration 0003: remote worker enrollment and node credentials (Week 2).
-- Only HMAC-SHA256 digests of bearer secrets are stored; raw secrets are shown once.
BEGIN;

ALTER TABLE worker_nodes ADD COLUMN revoked_at timestamptz;

CREATE TABLE enrollment_tokens (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  token_hash char(64) NOT NULL UNIQUE,
  resource_id uuid NOT NULL REFERENCES resources(id),
  issued_by uuid NOT NULL REFERENCES users(id),
  expires_at timestamptz NOT NULL,
  used_at timestamptz,
  worker_id uuid REFERENCES worker_nodes(id),
  revoked_at timestamptz,
  created_at timestamptz NOT NULL DEFAULT now(),
  CONSTRAINT enrollment_single_use CHECK ((used_at IS NULL) = (worker_id IS NULL)),
  CONSTRAINT enrollment_expiry_after_issue CHECK (expires_at > created_at)
);

CREATE TABLE node_credentials (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  worker_id uuid NOT NULL REFERENCES worker_nodes(id),
  kind text NOT NULL CHECK (kind IN ('NODE_KEY','ACCESS')),
  parent_id uuid REFERENCES node_credentials(id),
  secret_hash char(64) NOT NULL UNIQUE,
  issued_at timestamptz NOT NULL DEFAULT now(),
  expires_at timestamptz NOT NULL,
  revoked_at timestamptz,
  CONSTRAINT access_token_has_parent CHECK ((kind = 'ACCESS') = (parent_id IS NOT NULL)),
  CONSTRAINT credential_expiry_after_issue CHECK (expires_at > issued_at)
);
CREATE INDEX idx_node_credentials_worker ON node_credentials(worker_id) WHERE revoked_at IS NULL;

-- Defence in depth: enrollment (the only path to a remote node) is impossible for a
-- PERSONAL_SUBSCRIPTION resource, regardless of application code.
CREATE FUNCTION forbid_personal_enrollment() RETURNS trigger AS $$
BEGIN
  IF EXISTS (SELECT 1 FROM resources r WHERE r.id = NEW.resource_id
             AND r.resource_type = 'PERSONAL_SUBSCRIPTION') THEN
    RAISE EXCEPTION 'PERSONAL_SUBSCRIPTION resources cannot enroll remote workers'
      USING ERRCODE = 'check_violation';
  END IF;
  RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER trg_forbid_personal_enrollment
  BEFORE INSERT OR UPDATE OF resource_id ON enrollment_tokens
  FOR EACH ROW EXECUTE FUNCTION forbid_personal_enrollment();

COMMIT;

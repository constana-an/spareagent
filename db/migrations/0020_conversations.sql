BEGIN;
CREATE INDEX idx_tasks_conversation ON tasks ((spec->>'conversation_id'))
  WHERE resource_scope = 'EXPERIMENTAL_SHARED_SUBSCRIPTION';
COMMIT;

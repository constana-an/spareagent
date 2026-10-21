-- Incremental migration for capability-based dispatch (docs/GENERAL_TASKS_DESIGN.md §5,
-- step C). Prior migrations remain immutable.
--
-- * allowed_tools: the tools an owner lets shared tasks use on their device. A task runs
--   only when its kind's required tools are both present on the device (declared by the
--   worker from its own probes) and allowed here.
BEGIN;
ALTER TABLE subscription_sharing_preferences
  ADD COLUMN allowed_tools jsonb NOT NULL DEFAULT '["code","doc","git","python-data"]'::jsonb;
ALTER TABLE subscription_sharing_preferences ADD CONSTRAINT allowed_tools_is_array
  CHECK (jsonb_typeof(allowed_tools) = 'array');
COMMIT;

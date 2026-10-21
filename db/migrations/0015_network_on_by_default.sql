-- Operator decision 2026-10-21 (D-97): new devices allow internet tasks by default. The
-- owner can still switch "network" off in the Mac app; existing preferences are unchanged.
-- Prior migrations remain immutable.
BEGIN;

ALTER TABLE subscription_sharing_preferences
  ALTER COLUMN allowed_tools SET DEFAULT '["code","doc","git","network","python-data"]'::jsonb;

COMMIT;

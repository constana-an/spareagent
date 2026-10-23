-- NULL represents no task-count or execution-time limit. Existing settings stay intact.
BEGIN;

ALTER TABLE experimental_group_members
  ADD COLUMN access_paused boolean NOT NULL DEFAULT false;

ALTER TABLE experimental_groups
  ALTER COLUMN max_tasks_per_day DROP NOT NULL,
  ALTER COLUMN max_task_duration_seconds DROP NOT NULL;

ALTER TABLE subscription_sharing_preferences
  ALTER COLUMN max_tasks_per_day DROP NOT NULL,
  ALTER COLUMN max_task_duration_seconds DROP NOT NULL;

ALTER TABLE tasks ALTER COLUMN max_duration_seconds DROP NOT NULL;
ALTER TABLE tasks ADD CONSTRAINT task_duration_required_unless_experimental
  CHECK (max_duration_seconds IS NOT NULL OR resource_scope = 'EXPERIMENTAL_SHARED_SUBSCRIPTION');

COMMIT;

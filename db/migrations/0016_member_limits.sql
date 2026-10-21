-- Per-member limits (D-98): the owner can cap how many tasks and how many execution
-- minutes each member may use on the owner's device per rolling 24 hours. NULL means only
-- the group/device limits apply. Prior migrations remain immutable.
BEGIN;

ALTER TABLE experimental_group_members
  ADD COLUMN max_tasks_per_day int CHECK (max_tasks_per_day BETWEEN 1 AND 50),
  ADD COLUMN max_minutes_per_day int CHECK (max_minutes_per_day BETWEEN 1 AND 1440);

COMMIT;

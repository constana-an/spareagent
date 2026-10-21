-- General tasks (docs/GENERAL_TASKS_DESIGN.md §11): one open-ended kind for discussion,
-- writing, small file edits and code-drawn images, with optional attachments and no
-- repository. Starts DEVELOPING like every new kind (design §7). Prior migrations remain
-- immutable.
BEGIN;

ALTER TABLE task_kinds DROP CONSTRAINT task_kinds_category_check;
ALTER TABLE task_kinds ADD CONSTRAINT task_kinds_category_check CHECK (category IN
  ('WRITING','INFORMATION','RESEARCH','DATA','CODE','FILE_TOOLS','GENERAL'));

INSERT INTO task_kinds (kind, category, needs_repository, status, acceptance_report)
VALUES ('GENERAL', 'GENERAL', false, 'DEVELOPING', NULL);

COMMIT;

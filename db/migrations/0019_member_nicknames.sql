-- Member-provided names; existing members keep their identity and owner-local notes.
BEGIN;

ALTER TABLE experimental_group_members ADD COLUMN nickname text
  CHECK (nickname IS NULL OR (length(btrim(nickname)) BETWEEN 1 AND 40));

COMMIT;

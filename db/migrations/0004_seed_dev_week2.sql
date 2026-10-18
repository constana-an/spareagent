-- LOCAL DEVELOPMENT ONLY. Never run in production. Week 2 fixtures (repeatable).
BEGIN;
INSERT INTO users(id,email,role) VALUES
('55555555-5555-4555-8555-555555555555','demo-admin@local.test','ADMIN')
ON CONFLICT (id) DO NOTHING;
-- A personal subscription resource exists only to demonstrate that it is never schedulable
-- or enrollable. It is not publicly eligible (the DB constraint forbids it anyway).
INSERT INTO resources(id,owner_id,resource_type,authorization_status,public_eligible,license_reference)
VALUES ('66666666-6666-4666-8666-666666666666','22222222-2222-4222-8222-222222222222','PERSONAL_SUBSCRIPTION','APPROVED',false,NULL)
ON CONFLICT (id) DO NOTHING;
COMMIT;

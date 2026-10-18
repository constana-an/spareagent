-- LOCAL DEVELOPMENT ONLY. Never run in production.
BEGIN;
INSERT INTO users(id,email,role) VALUES
('11111111-1111-4111-8111-111111111111','demo-consumer@local.test','CONSUMER'),
('22222222-2222-4222-8222-222222222222','demo-provider@local.test','PROVIDER')
ON CONFLICT (id) DO NOTHING;
INSERT INTO resources(id,owner_id,resource_type,authorization_status,public_eligible,license_reference)
VALUES ('33333333-3333-4333-8333-333333333333','22222222-2222-4222-8222-222222222222','MANAGED_API','APPROVED',true,'LOCAL_MOCK_ONLY')
ON CONFLICT (id) DO NOTHING;
INSERT INTO worker_nodes(id,resource_id,name,status,capabilities,max_concurrency)
VALUES ('44444444-4444-4444-8444-444444444444','33333333-3333-4333-8333-333333333333','mock-local','IDLE','{"code":true,"chat":true,"languages":["python","typescript"]}',1)
ON CONFLICT (id) DO NOTHING;
COMMIT;

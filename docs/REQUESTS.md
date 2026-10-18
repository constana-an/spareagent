# Sample HTTP requests — local development only

Seeded demo consumer: `11111111-1111-4111-8111-111111111111`.
In W1 DEV_AUTH_ONLY=true, use `X-Dev-User-ID` **only from localhost**. Replace with real OAuth/JWT before deployment.

## Create a mock task
```bash
curl -i http://localhost:8000/v1/tasks \
  -H 'Content-Type: application/json' \
  -H 'X-Dev-User-ID: 11111111-1111-4111-8111-111111111111' \
  -H 'Idempotency-Key: example-task-0001' \
  -d '{"task_type":"WRITE_TESTS","repository_url":"https://github.com/example/public-fixture","commit_sha":"0123456789abcdef0123456789abcdef01234567","prompt":"Write two tests for the demo fixture","max_budget_cents":500,"max_duration_seconds":120}'
```
Expected `202` with task `id` and current `status`. W1 MOCK does not clone or execute the example URL.

## Get task
```bash
curl -H 'X-Dev-User-ID: 11111111-1111-4111-8111-111111111111' http://localhost:8000/v1/tasks/TASK_UUID
```

## Stream committed events
```bash
curl -N -H 'X-Dev-User-ID: 11111111-1111-4111-8111-111111111111' http://localhost:8000/v1/tasks/TASK_UUID/events
```

## Gateway mock
```bash
curl http://localhost:8000/v1/chat/completions \
  -H 'Content-Type: application/json' \
  -H 'X-Dev-User-ID: 11111111-1111-4111-8111-111111111111' \
  -d '{"model":"mock-chat","stream":false,"messages":[{"role":"user","content":"Say hello"}]}'
```
Expected `200` with `chat.completion` object and usage counters.

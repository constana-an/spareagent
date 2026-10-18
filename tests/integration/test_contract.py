"""Contract tests: openapi.yaml validity, route coverage, and response shapes."""

from pathlib import Path

import pytest
import yaml
from jsonschema import Draft202012Validator
from openapi_spec_validator import validate

from tests.conftest import USER_A, create_task, drain_worker, headers

ROOT = Path(__file__).resolve().parents[2]
SPEC = yaml.safe_load((ROOT / "contracts" / "openapi.yaml").read_text())


def schema_validator(ref: str) -> Draft202012Validator:
    return Draft202012Validator({"$ref": ref, "components": SPEC["components"]})


def response_schema(path: str, method: str, status: str) -> dict:
    resp = SPEC["paths"][path][method]["responses"][status]
    if "$ref" in resp:
        name = resp["$ref"].rsplit("/", 1)[-1]
        resp = SPEC["components"]["responses"][name]
    return {**resp["content"]["application/json"]["schema"], "components": SPEC["components"]}


def test_openapi_document_is_valid():
    validate(SPEC)
    assert SPEC["openapi"].startswith("3.1")


@pytest.mark.db
async def test_every_contract_operation_is_implemented(app):
    generated = app.openapi()["paths"]
    for path, ops in SPEC["paths"].items():
        assert path in generated, f"missing route {path}"
        for method, op in ops.items():
            assert method in generated[path], f"missing {method.upper()} {path}"
            assert generated[path][method]["operationId"] == op["operationId"]


@pytest.mark.db
async def test_no_undocumented_routes(app):
    contract = set(SPEC["paths"])
    implemented = {p for p in app.openapi()["paths"]}
    assert implemented == contract, implemented ^ contract


@pytest.mark.db
async def test_responses_match_contract_schemas(client, ctx):
    created = await create_task(client)
    schema_validator("#/components/schemas/Task").validate(created.json())
    task_id = created.json()["id"]
    await drain_worker(ctx)

    Draft202012Validator(response_schema("/v1/tasks", "get", "200")).validate(
        (await client.get("/v1/tasks", headers=headers(USER_A))).json()
    )
    schema_validator("#/components/schemas/Task").validate(
        (await client.get(f"/v1/tasks/{task_id}", headers=headers(USER_A))).json()
    )
    Draft202012Validator(response_schema("/v1/tasks/{task_id}/artifacts", "get", "200")).validate(
        (await client.get(f"/v1/tasks/{task_id}/artifacts", headers=headers(USER_A))).json()
    )
    Draft202012Validator(response_schema("/v1/models", "get", "200")).validate(
        (await client.get("/v1/models", headers=headers(USER_A))).json()
    )
    chat = await client.post(
        "/v1/chat/completions",
        json={"model": "mock-chat", "messages": [{"role": "user", "content": "hi"}]},
        headers=headers(USER_A),
    )
    schema_validator("#/components/schemas/ChatResponse").validate(chat.json())
    error = await client.get(
        "/v1/tasks/00000000-0000-4000-8000-000000000000", headers=headers(USER_A)
    )
    schema_validator("#/components/schemas/ApiError").validate(error.json())
    assert set(error.json()["error"]) == {"code", "message", "request_id"}


@pytest.mark.db
async def test_request_id_propagates(client):
    resp = await client.get("/healthz", headers={"X-Request-ID": "trace-abc-123"})
    assert resp.headers["x-request-id"] == "trace-abc-123"
    resp = await client.get("/healthz", headers={"X-Request-ID": "bad id with spaces"})
    assert resp.headers["x-request-id"] != "bad id with spaces"

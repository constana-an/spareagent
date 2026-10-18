"""Parse and validate contracts/openapi.yaml (OpenAPI 3.1) and the event JSON schema."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import yaml
from jsonschema import Draft202012Validator
from openapi_spec_validator import validate

ROOT = Path(__file__).resolve().parent.parent


def main() -> int:
    spec = yaml.safe_load((ROOT / "contracts" / "openapi.yaml").read_text(encoding="utf-8"))
    validate(spec)
    events = json.loads((ROOT / "contracts" / "events.schema.json").read_text(encoding="utf-8"))
    Draft202012Validator.check_schema(events)
    print(
        f"OK: openapi {spec['openapi']} with {len(spec['paths'])} paths; "
        "events.schema.json is a valid 2020-12 schema"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())

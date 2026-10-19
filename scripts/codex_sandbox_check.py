"""Compatibility entry point: no-inference sandbox probe."""

import argparse
import json

from packages.worker.sandbox_check import check

if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--codex-bin", default="codex")
    result = check(p.parse_args().codex_bin)
    print(json.dumps(result, indent=2))
    raise SystemExit(0 if result["passed"] else 1)

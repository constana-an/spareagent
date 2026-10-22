"""Scripts run as `python scripts/<name>.py`, which puts scripts/ first on sys.path."""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_no_script_shadows_a_standard_library_module():
    # e.g. scripts/operator.py once replaced the stdlib `operator` for every script there.
    shadowing = sorted(
        p.name for p in (ROOT / "scripts").glob("*.py") if p.stem in sys.stdlib_module_names
    )
    assert shadowing == []

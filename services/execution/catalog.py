"""Allowlist of repositories the deterministic executor may modify (Week 3).

There is no autonomous code generation: each entry pins a repository + commit, a fixed set of
exact text replacements, and the TRUSTED commands used to test (worker) and verify (platform).
Commands come from here, never from repository content. A future model-backed AgentAdapter
replaces only the "edits" step; checkout, sandboxing, patch policy and verification stay.
"""

from __future__ import annotations

from dataclasses import dataclass

FIXTURE_BASE = "https://github.com/spareagent-fixtures"
UNITTEST = ("python3", "-m", "unittest", "-v")
NODE_TEST = (
    "node",
    "--experimental-strip-types",
    "--test",
    "--test-reporter=tap",
    "test/strings.test.ts",
)


@dataclass(frozen=True)
class Edit:
    path: str
    old: str
    new: str


@dataclass(frozen=True)
class Recipe:
    repository: str  # owner/name
    commit_sha: str
    language: str  # "python" | "typescript"
    framework: str  # "unittest" | "node-tap"
    edits: tuple[Edit, ...]
    test_command: tuple[str, ...]
    verify_command: tuple[str, ...]
    allowed_paths: frozenset[str]
    expected_passed: int
    summary: str


CATALOG: tuple[Recipe, ...] = (
    Recipe(
        repository="spareagent-fixtures/python-calc",
        commit_sha="44d957e53add3b73f7eaaa9755b1525ff32bb8c3",
        language="python",
        framework="unittest",
        edits=(Edit("calc.py", "    return a - b\n", "    return a + b\n"),),
        test_command=UNITTEST,
        verify_command=UNITTEST,
        allowed_paths=frozenset({"calc.py"}),
        expected_passed=3,
        summary="Fix add(): it subtracted instead of adding.",
    ),
    Recipe(
        repository="spareagent-fixtures/ts-strings",
        commit_sha="115e49e8bffba58e74c029279a772fde67ffe9a6",
        language="typescript",
        framework="node-tap",
        edits=(
            Edit(
                "src/strings.ts",
                "  return s.toLowerCase();\n",
                "  return s.charAt(0).toUpperCase() + s.slice(1);\n",
            ),
        ),
        test_command=NODE_TEST,
        verify_command=NODE_TEST,
        allowed_paths=frozenset({"src/strings.ts"}),
        expected_passed=3,
        summary="Fix capitalize(): it lower-cased the whole string.",
    ),
    Recipe(
        repository="spareagent-fixtures/python-probe",
        commit_sha="5ba6b859e9b8819a030de9f54a80c5c815f42e48",
        language="python",
        framework="unittest",
        edits=(
            Edit(
                "greet.py",
                "    return 'Hello ' + name.upper()\n",
                "    return 'Hello ' + name.capitalize()\n",
            ),
        ),
        test_command=UNITTEST,
        verify_command=UNITTEST,
        allowed_paths=frozenset({"greet.py"}),
        expected_passed=6,
        summary="Fix greet() casing; isolation probes must also pass inside the sandbox.",
    ),
)


def lookup(
    repository_url: str, commit_sha: str, catalog: tuple[Recipe, ...] = CATALOG
) -> Recipe | None:
    url = repository_url.rstrip("/")
    if url.endswith(".git"):
        url = url[:-4]
    for recipe in catalog:
        if (
            url == f"https://github.com/{recipe.repository}"
            and commit_sha.lower() == recipe.commit_sha
        ):
            return recipe
    return None


# LIVE WRITE_TESTS has a separate recipe: immutable correct source and baseline tests;
# only one additional test module may change. Passing proves execution, not test quality.
LIVE_WRITE_TESTS_CATALOG = (
    Recipe(
        repository="spareagent-fixtures/python-test-target",
        commit_sha="0c1bded2a0a0461d1f96d20b5c5df0c6dd68c6cb",
        language="python",
        framework="unittest",
        edits=(),
        test_command=UNITTEST,
        verify_command=UNITTEST,
        allowed_paths=frozenset({"test_added.py"}),
        expected_passed=4,
        summary="Run three immutable baseline tests plus one added unittest.",
    ),
)

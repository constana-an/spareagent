#!/usr/bin/env python3
"""Deterministic stand-in for the official Codex CLI (tests only; no network, no auth).

Behaviour is selected by `$CODEX_HOME/mode` (CODEX_HOME is the only channel that survives
the adapter's environment allow-list, exactly like a real owner's Codex home). Every
invocation records its argv and environment *names and API-key presence* to
`$CODEX_HOME/calls.jsonl` so tests can prove no API key reached the child.
"""

import json
import os
import sys
import time

home = os.environ.get("CODEX_HOME", "")
mode = "chatgpt"
if home and os.path.exists(os.path.join(home, "mode")):
    mode = open(os.path.join(home, "mode")).read().strip()

if home:
    with open(os.path.join(home, "calls.jsonl"), "a") as fh:
        fh.write(
            json.dumps(
                {
                    "argv": sys.argv[1:],
                    "env_names": sorted(os.environ),
                    "api_key_seen": any(
                        os.environ.get(k) for k in ("OPENAI_API_KEY", "CODEX_API_KEY")
                    ),
                }
            )
            + "\n"
        )

args = sys.argv[1:]
if args == ["--version"]:
    print("codex-cli 0.163.0")
    sys.exit(0)
if args == ["exec", "--help"]:
    print("--json --ephemeral --ignore-user-config --ignore-rules")
    sys.exit(0)
if args[:2] == ["login", "status"]:
    if mode in ("logged_out",):
        print("Not logged in")
        sys.exit(1)
    if mode == "apikey":
        print("Logged in using an API key - sk-proj-****")
        sys.exit(0)
    if mode == "garbled":
        print("???")
        sys.exit(0)
    print("Logged in using ChatGPT")
    sys.exit(0)

if "exec" not in args:
    print("unsupported", file=sys.stderr)
    sys.exit(2)

if 'forced_login_method="chatgpt"' not in args:
    print("fake: refusing to run without forced_login_method", file=sys.stderr)
    sys.exit(3)

prompt = sys.stdin.read()
if home:
    with open(os.path.join(home, "last_prompt.txt"), "w") as fh:
        fh.write(prompt)
sandbox = args[args.index("--sandbox") + 1] if "--sandbox" in args else "read-only"
if any('":workspace_roots"="write"' in a for a in args):
    sandbox = "workspace-write"
if sandbox == "workspace-write" and mode == "chatgpt" and not os.path.isdir("in"):
    with open("test_added_by_codex.py", "w") as fh:
        fh.write("def test_added():\n    assert True\n")

# Document tasks (packages/worker/recipes.py): in/ holds inputs, results go to out/.
if os.path.isdir("in") and os.path.isdir("out") and mode.startswith(("chatgpt", "doc_")):
    if os.path.exists("in/paragraphs.json"):
        source = json.load(open("in/paragraphs.json"))
        if mode == "doc_untranslated":
            texts = source
        elif mode == "doc_wrong_count":
            texts = source[:-1] or ["x"]
        else:
            texts = [f"これは第{i + 1}段落の翻訳です。" for i in range(len(source))]
        if mode != "doc_missing":
            with open("out/translation.json", "w") as fh:
                json.dump(texts, fh, ensure_ascii=False)
    else:
        names = [
            line.split("in/", 1)[1].split()[0]
            for line in prompt.splitlines()
            if line.startswith("- in/")
        ]
        if mode != "doc_missing":
            with open("out/report.md", "w") as fh:
                fh.write("# Analysis\n\nInputs: " + ", ".join(names) + "\n\n")
                fh.write("Mean of column value = 2.0 (sum 6 / count 3). " * 6 + "\n")
            with open("out/chart-1.svg", "w") as fh:
                fh.write(
                    '<svg xmlns="http://www.w3.org/2000/svg"><rect width="1" height="2"/></svg>'
                )
            with open("out/tool.exe", "w") as fh:
                fh.write("dropped")
            os.symlink("/etc/passwd", "out/leak.csv")
            if mode == "doc_active_svg":
                with open("out/chart-2.svg", "w") as fh:
                    fh.write('<svg xmlns="http://www.w3.org/2000/svg" onload="alert(1)"/>')


def emit(obj):
    print(json.dumps(obj), flush=True)


emit({"type": "thread.started", "thread_id": "fake-thread-0001"})
emit({"type": "turn.started"})
if mode == "expired":
    emit({"type": "turn.failed", "error": {"message": "401 Unauthorized: refresh token expired"}})
    sys.exit(1)
if mode == "limit":
    emit(
        {
            "type": "error",
            "message": "You've hit your usage limit. Try again in 2 hours 13 minutes.",
        }
    )
    sys.exit(1)
if mode == "crash":
    print("boom: internal error", file=sys.stderr)
    sys.exit(70)
emit({"type": "item.started", "item": {"id": "i1", "type": "command_execution", "command": "ls"}})
if mode == "slow":
    time.sleep(30)
emit(
    {
        "type": "item.completed",
        "item": {"id": "i1", "type": "command_execution", "status": "completed"},
    }
)
emit(
    {
        "type": "item.completed",
        "item": {
            "id": "i2",
            "type": "agent_message",
            "text": f"Reviewed: {prompt.strip()[:60]} (token=abcd1234secret)",
        },
    }
)
emit(
    {
        "type": "turn.completed",
        "usage": {
            "input_tokens": 120,
            "cached_input_tokens": 20,
            "output_tokens": 40,
            "reasoning_output_tokens": 8,
        },
    }
)
sys.exit(0)

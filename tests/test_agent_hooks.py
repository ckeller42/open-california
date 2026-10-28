"""The Claude Code hook scripts in ``tools/hooks/`` (wired per user, see its README): fed the hook
event JSON on stdin exactly as Claude Code sends it."""

import json
import subprocess
import sys
from pathlib import Path

import pytest

HOOKS = Path(__file__).resolve().parent.parent / "tools" / "hooks"


def run_hook(name, event):
    return subprocess.run(
        [sys.executable, str(HOOKS / name)], input=json.dumps(event), capture_output=True, text=True
    )


@pytest.mark.parametrize(
    "cmd",
    [
        "git commit --no-verify -m x",
        "git commit -n -m x",
        "git commit -qn -F msg.txt",
        "cd /tmp/w && git push --no-verify origin b",
        "git -C /tmp/w commit -am x --no-verify",
        "git merge --no-verify main",
    ],
)
def test_block_no_verify_refuses_a_hook_bypass(cmd):
    r = run_hook("block-no-verify.py", {"tool_name": "Bash", "tool_input": {"command": cmd}})
    assert r.returncode == 2 and r.stderr.startswith("Blocked by block-no-verify hook")


@pytest.mark.parametrize(
    "cmd",
    [
        "git commit -q -F msg.txt",
        "git push -n origin b",  # push -n is a dry run, not a bypass
        "grep -n -- --no-verify docs/x.md",
        "echo 'never git commit --no-verify'",
        "git log --oneline -3",
    ],
)
def test_block_no_verify_lets_other_commands_through(cmd):
    r = run_hook("block-no-verify.py", {"tool_name": "Bash", "tool_input": {"command": cmd}})
    assert r.returncode == 0 and r.stderr == ""


def test_block_no_verify_fails_open_on_a_malformed_event():
    r = subprocess.run(
        [sys.executable, str(HOOKS / "block-no-verify.py")], input="{", text=True, capture_output=True
    )
    assert r.returncode == 0


def test_ruff_on_edit_formats_and_fixes_the_edited_python_file(tmp_path):
    f = tmp_path / "m.py"
    f.write_text("import sys\nimport os\nx = {'a':1}\n")
    r = run_hook("ruff-on-edit.py", {"tool_name": "Edit", "tool_input": {"file_path": str(f)}})
    assert r.returncode == 0
    assert f.read_text() == 'x = {"a": 1}\n'  # unused imports removed (F401 fix), then formatted


def test_ruff_on_edit_reports_what_it_cannot_fix(tmp_path):
    f = tmp_path / "m.py"
    f.write_text("def f():\n    return undefined_name\n")
    r = run_hook("ruff-on-edit.py", {"tool_name": "Write", "tool_input": {"file_path": str(f)}})
    out = json.loads(r.stdout)["hookSpecificOutput"]
    assert r.returncode == 0 and out["hookEventName"] == "PostToolUse"
    assert "F821" in out["additionalContext"]


def test_ruff_on_edit_ignores_other_files(tmp_path):
    f = tmp_path / "m.js"
    f.write_text("x = {'a':1}\n")
    r = run_hook("ruff-on-edit.py", {"tool_name": "Edit", "tool_input": {"file_path": str(f)}})
    assert r.returncode == 0 and r.stdout == "" and f.read_text() == "x = {'a':1}\n"

"""Tests that must share one xdist worker really do, under the --dist mode CI and tools/ci.sh use.

``--dist loadgroup`` silently stopped working with pytest 9 + pytest-xdist 3.8 (xdist renames
``item._nodeid``, pytest 9 reads ``item._id``): every ``xdist_group`` mark was ignored and
tests/e2e/test_gui.py, whose tests share one daemon and browser, got spread over the workers.
The suite now uses ``--dist loadfile`` (a file is the group). These checks fail if a parallel
pytest call drops back to a mode that splits a group, or if a group spans files.
"""

import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
# Every place that starts a parallel pytest run.
RUNNERS = [ROOT / ".github" / "workflows" / "ci.yml", ROOT / "tools" / "ci.sh"]


def _parallel_calls():
    """(file, line) of every pytest call with ``-n``."""
    for path in RUNNERS:
        for line in path.read_text().splitlines():
            if re.search(r"(pytest|\$\{run\[@\]\}).* -n ", line):
                yield path.name, line.strip()


def _dist_mode(line):
    m = re.search(r"--dist[ =](\w+)", line)
    return m.group(1) if m else "load"  # xdist's default: ignores groups and files


def test_every_parallel_run_names_its_dist_mode():
    bare = [(name, line) for name, line in _parallel_calls() if "--dist" not in line]
    assert not bare, "parallel pytest without --dist: %s" % bare


def test_an_xdist_group_never_spans_two_files():
    """Under ``--dist loadfile`` only the file keeps tests together: a group in two files would
    run on two workers."""
    groups = {}
    for path in (ROOT / "tests").rglob("*.py"):
        for name in re.findall(r"xdist_group\(\s*[\"']([^\"']+)", path.read_text()):
            groups.setdefault(name, set()).add(path.relative_to(ROOT).as_posix())
    split = {g: sorted(f) for g, f in groups.items() if len(f) > 1}
    assert not split, "xdist_group in more than one file (use one file, or a lock): %s" % split


GROUPED = """
import os, time, pytest
pytestmark = pytest.mark.xdist_group("shared")

@pytest.mark.parametrize("i", range(8))
def test_grouped(i):
    time.sleep(0.05)
    with open(os.environ["WORKERS_OUT"], "a") as f:
        f.write(os.environ.get("PYTEST_XDIST_WORKER", "none") + "\\n")
"""


@pytest.mark.parametrize("mode", sorted({_dist_mode(line) for _, line in _parallel_calls()}))
def test_dist_mode_keeps_a_group_on_one_worker(mode, tmp_path):
    pytest.importorskip("xdist")
    (tmp_path / "test_grouped.py").write_text(GROUPED)
    (tmp_path / "test_other.py").write_text("def test_a():\n    pass\n\ndef test_b():\n    pass\n")
    out = tmp_path / "workers.txt"
    r = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "-q",
            "-p",
            "no:cacheprovider",
            "-n",
            "2",
            "--dist",
            mode,
            str(tmp_path),
        ],
        cwd=tmp_path,
        env={**os.environ, "WORKERS_OUT": str(out)},
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert r.returncode == 0, r.stdout + r.stderr
    workers = out.read_text().split()
    assert len(workers) == 8
    assert len(set(workers)) == 1, "--dist %s spread one xdist_group over %s" % (mode, sorted(set(workers)))

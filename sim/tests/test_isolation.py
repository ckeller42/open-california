"""Guard: sim/ stands alone (it may move to its own repository). No Python file imports the satellite's
packages and no C file includes the satellite's headers."""

import ast
import re
from pathlib import Path

SIM = Path(__file__).resolve().parents[1]
FORBIDDEN = {"calictl", "tools", "firmware", "tests"}


def test_no_python_file_imports_the_satellite():
    bad = []
    for f in SIM.rglob("*.py"):
        for node in ast.walk(ast.parse(f.read_text())):
            names = [a.name for a in node.names] if isinstance(node, ast.Import) else []
            if isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                names = [node.module]
            bad += [f"{f.relative_to(SIM)}: {n}" for n in names if n.split(".")[0] in FORBIDDEN]
    assert not bad


def test_no_c_file_includes_the_satellite():
    inc = re.compile(r'#\s*include\s*[<"]([^>"]+)[>"]')
    bad = [
        f"{f.relative_to(SIM)}: {m}"
        for f in list(SIM.rglob("*.c")) + list(SIM.rglob("*.h"))
        for m in inc.findall(f.read_text())
        if m.startswith(("cali_", "../")) or "firmware/" in m
    ]
    assert not bad

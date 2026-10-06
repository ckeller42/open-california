"""Guard: the write-side twin of test_signal_coverage — every command calictl can build is documented.

The accepted ``(function, what)`` pairs are read from ``control.BUILDERS`` itself (each builder's
``what == "x"`` / ``what in (...)`` branches, roof's ``ROOF_MOVES``, lighting's ``LIGHT_ZONES`` as
the ``zone`` row). Each must have a row in control-and-actuation.md §5 whose Status names an
evidence tier from evidence-ledger.md, and a retired command must not be listed there as live.
"""

import ast
import inspect
import re
import textwrap
from pathlib import Path

import pytest

from calictl import control, overrides, protocol

DOC = Path(__file__).resolve().parent.parent / "docs" / "business-logic" / "control-and-actuation.md"
TIER = re.compile(r"\b(CAPTURE|DEVICE|APP-RECORDED|APP-OBSERVED|DECOMPILE|DV)\b")
RETIRED = {("lighting", "color")}  # refused by the builder with a pointer to the replacement


def _whats(fn):
    """String constants a builder compares ``what`` against (``==`` or ``in (...)``)."""
    out = set()
    for node in ast.walk(ast.parse(textwrap.dedent(inspect.getsource(fn)))):
        if isinstance(node, ast.Compare) and getattr(node.left, "id", None) == "what":
            for c in node.comparators:
                elts = c.elts if isinstance(c, (ast.Tuple, ast.List, ast.Set)) else [c]
                out |= {e.value for e in elts if isinstance(e, ast.Constant) and isinstance(e.value, str)}
    return out


def _accepted():
    pairs = {(fn, w) for fn, b in control.BUILDERS.items() for w in _whats(b)}
    pairs |= {("roof", w) for w in control.ROOF_MOVES}
    pairs.add(("lighting", "zone"))  # every LIGHT_ZONES key (and raw BrightnessL* field) is one row
    return pairs - RETIRED


def _doc_rows():
    """§5 command table -> {(function, what): status}."""
    section = (
        DOC.read_text(encoding="utf-8").split("## 5. Control command surface", 1)[1].split("\n## ", 1)[0]
    )
    rows = {}
    for line in section.splitlines():
        cells = [c.strip() for c in re.split(r"(?<!\\)\|", line.strip().strip("|"))]
        if len(cells) < 5 or cells[0] in ("Function", "") or set(cells[0]) <= set("-"):
            continue
        cell = cells[1].replace("\\|", " ")
        whats = {t.split()[0] for t in re.findall(r"`([^`]+)`", cell)}
        if re.search(r"\bzone\b", re.sub(r"`[^`]*`", "", cell)):
            whats.add("zone")
        for w in whats:
            rows[(cells[0], w)] = cells[-1]
    return rows


def test_parser_sees_the_builders():
    acc = _accepted()
    assert {("cooler", "power"), ("lighting", "wakeup"), ("roof", "stop"), ("airheater", "permanent")} <= acc


def test_every_command_is_documented_with_an_evidence_tier():
    rows = _doc_rows()
    missing = sorted(p for p in _accepted() if p not in rows)
    assert not missing, (
        "control.BUILDERS accepts commands with no row in control-and-actuation.md §5 "
        "(add one with its evidence tier from evidence-ledger.md): %s" % missing
    )
    untiered = sorted(p for p in _accepted() if not TIER.search(rows[p]))
    assert not untiered, "§5 rows without an evidence tier (%s): %s" % (TIER.pattern, untiered)


def test_no_stale_rows():
    stale = sorted(set(_doc_rows()) - _accepted() - RETIRED)
    assert not stale, "§5 documents commands control.BUILDERS does not accept: %s" % stale


@pytest.mark.parametrize("function,what", sorted(RETIRED))
def test_retired_commands_are_refused_and_not_listed_as_live(function, what):
    funcs = protocol.load()
    overrides.apply(funcs)
    with pytest.raises(control.CommandError, match="retired"):
        control.build(funcs, function, what, "red", {})
    assert (function, what) not in _doc_rows(), "%s %s is retired but listed as live in §5" % (function, what)

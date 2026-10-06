"""Guard: Playwright anti-patterns in tests/e2e that make a test pass without testing anything.

``page.wait_for_function("async () => ...")`` never waits: Playwright polls until the predicate's
return value is truthy, and an async predicate returns a Promise, which is always truthy — so the
wait resolves on the first poll and the assertion after it races the daemon.
"""

import ast
from pathlib import Path

E2E = Path(__file__).resolve().parent / "e2e"


def _predicate(call):
    arg = call.args[0] if call.args else next((k.value for k in call.keywords if k.arg == "expression"), None)
    return arg.value if isinstance(arg, ast.Constant) and isinstance(arg.value, str) else None


def test_wait_for_function_predicates_are_not_async():
    bad = []
    for path in sorted(E2E.rglob("*.py")):
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if isinstance(node, ast.Call) and getattr(node.func, "attr", None) == "wait_for_function":
                pred = _predicate(node)
                if pred is not None and pred.lstrip().startswith("async"):
                    bad.append(
                        "%s:%d: %s" % (path.relative_to(E2E.parent.parent), node.lineno, pred.strip()[:70])
                    )
    assert not bad, (
        "wait_for_function with an async JS predicate never waits (the returned Promise is truthy, so "
        "it resolves on the first poll). Fix: use a synchronous predicate over page state, or poll "
        "/api/state from Python (page.request.get(...) in a loop with a deadline):\n" + "\n".join(bad)
    )

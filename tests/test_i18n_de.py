"""Guard: every simple t()/tf() key in the web UI has a German translation.

The UI localizes by wrapping English source strings in `t()` / `tf()` (see calictl/webui/app.js);
a missing key in `strings.de.js` silently falls back to English. This test catches that drift for
the common case — a single string-literal key. Keys assembled by concatenation or computed from a
variable are maintained by hand and can't be extracted statically, so they're out of scope here.
"""
import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
APP = ROOT / "calictl" / "webui" / "app.js"
DE = ROOT / "calictl" / "webui" / "strings.de.js"


def _de_keys():
    """The keys of window.STRINGS_DE, via node so concatenated/computed keys resolve natively."""
    node = shutil.which("node")
    if not node:
        pytest.skip("node not available")
    out = subprocess.check_output(
        [node, "-e",
         "global.window={};require(process.argv[1]);"
         "process.stdout.write(JSON.stringify(Object.keys(window.STRINGS_DE)))",
         str(DE)],
        text=True,
    )
    return set(json.loads(out))


# single-literal first argument to t("...") or tf("...", ...); concatenations (quote then + / ])
# and variable args (t(x), t(a ? "b" : "c")) don't match and are intentionally skipped.
_T = re.compile(r'\bt\(\s*"((?:[^"\\]|\\.)*)"\s*\)')
_TF = re.compile(r'\btf\(\s*"((?:[^"\\]|\\.)*)"\s*,')


def test_every_simple_t_key_has_a_german_translation():
    keys = _de_keys()
    src = APP.read_text(encoding="utf-8")
    lits = set(_T.findall(src)) | set(_TF.findall(src))
    lits = {k for k in lits if k}          # drop the empty string
    missing = sorted(k for k in lits if k not in keys)
    assert not missing, "t()/tf() keys with no strings.de.js entry: " + repr(missing)


def test_de_values_are_nonempty_strings():
    """A DE entry that is empty or missing would render blank instead of falling back."""
    node = shutil.which("node")
    if not node:
        pytest.skip("node not available")
    out = subprocess.check_output(
        [node, "-e",
         "global.window={};require(process.argv[1]);"
         "process.stdout.write(JSON.stringify(window.STRINGS_DE))",
         str(DE)],
        text=True,
    )
    m = json.loads(out)
    bad = sorted(k for k, v in m.items() if not isinstance(v, str) or not v.strip())
    assert not bad, "empty/non-string German values: " + repr(bad)

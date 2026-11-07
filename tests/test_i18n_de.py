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
REMOTE = ROOT / "calictl" / "webui" / "remote.js"  # the remote app's screens (R_UI_REMOTE_APP)
DE = ROOT / "calictl" / "webui" / "strings.de.js"
REMOTE_DE = ROOT / "calictl" / "webui" / "strings.remote.de.js"  # the remote app only, merged into STRINGS_DE


def _de_keys(*files):
    """The keys of window.STRINGS_DE after loading ``files`` (default: both catalogs), via node so
    concatenated/computed keys resolve natively."""
    node = shutil.which("node")
    if not node:
        pytest.skip("node not available")
    files = files or (DE, REMOTE_DE)
    loads = "".join("require(process.argv[%d]);" % (i + 1) for i in range(len(files)))
    out = subprocess.check_output(
        [
            node,
            "-e",
            "global.window={};"
            + loads
            + "process.stdout.write(JSON.stringify(Object.keys(window.STRINGS_DE)))",
        ]
        + [str(f) for f in files],
        text=True,
    )
    return set(json.loads(out))


# single-literal first argument to t("...") / T("...") or tf("...", ...); concatenations (quote then + / ])
# and variable args (t(x), t(a ? "b" : "c")) don't match and are intentionally skipped.
_T = re.compile(r'\b[tT]\(\s*"((?:[^"\\]|\\.)*)"\s*\)')
_TF = re.compile(r'\btf\(\s*"((?:[^"\\]|\\.)*)"\s*,')

# app.js keys rendered only in remote mode (REMOTE branches): their German lives in strings.remote.de.js, which the
# satellite's bundle does not carry. Every other app.js key must be in strings.de.js itself.
APP_REMOTE_ONLY = {
    "LoRa · {ago}",
    "LoRa — display only",
    "Values via LoRa, {ago}",
    "Set up LoRa",
    "Scan the van's code",
    "Remove this van",
}


def _lits(path):
    src = path.read_text(encoding="utf-8")
    return {k for k in set(_T.findall(src)) | set(_TF.findall(src)) if k}  # drop the empty string


def test_every_simple_t_key_has_a_german_translation():
    local, both = _de_keys(DE), _de_keys()
    app = _lits(APP)
    missing = sorted(k for k in app - APP_REMOTE_ONLY if k not in local)
    assert not missing, "app.js t()/tf() keys with no strings.de.js entry: " + repr(missing)
    missing = sorted(k for k in (app & APP_REMOTE_ONLY) | _lits(REMOTE) if k not in both)
    assert not missing, "remote t()/tf() keys with no German entry: " + repr(missing)
    assert APP_REMOTE_ONLY <= app, "APP_REMOTE_ONLY names a key app.js no longer uses"


def test_de_values_are_nonempty_strings():
    """A DE entry that is empty or missing would render blank instead of falling back."""
    node = shutil.which("node")
    if not node:
        pytest.skip("node not available")
    out = subprocess.check_output(
        [
            node,
            "-e",
            "global.window={};require(process.argv[1]);require(process.argv[2]);"
            "process.stdout.write(JSON.stringify(window.STRINGS_DE))",
            str(DE),
            str(REMOTE_DE),
        ],
        text=True,
    )
    m = json.loads(out)
    bad = sorted(k for k, v in m.items() if not isinstance(v, str) or not v.strip())
    assert not bad, "empty/non-string German values: " + repr(bad)


def test_no_duplicate_de_keys():
    """A key written twice in strings.de.js: the object literal keeps the LAST value only, so one
    English string silently shows another German one (the pill's "Connecting…" once read the pairing
    wizard's "Verbindung wird aufgebaut…"). Node's Object.keys cannot see this; read the source."""
    keys = re.findall(
        r'^\s*("(?:[^"\\]|\\.)*")\s*:',
        DE.read_text(encoding="utf-8") + REMOTE_DE.read_text(encoding="utf-8"),
        re.M,
    )
    seen, dups = set(), set()
    for k in (json.loads(q) for q in keys):
        (dups if k in seen else seen).add(k)
    assert not dups, "keys defined twice in strings.de.js: " + repr(sorted(dups))

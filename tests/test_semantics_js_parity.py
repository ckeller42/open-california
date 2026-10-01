"""calictl/webui/semantics.js (the satellite UI's browser twin) equals calictl.semantics.

``tools.gen_semantics_vectors`` writes ``tests/vectors/semantics.json`` from the Python oracle
(:mod:`calictl.semantics`, :func:`calictl.anchors.check`, ``ServeBackend._firmware_meta``); this runs
``node`` over the same vectors through semantics.js — loaded as the browser loads it, a classic
script whose top-level names are globals — and asserts every output deep-equal (compared in Python,
so 12 == 12.0 and -0.0 == 0.0). Skipped only when node is absent (CI runners have it).

.. test:: The JS semantics twin equals the Python semantics on the golden vectors
   :id: T_SEMANTICS_JS_PARITY
   :links: R_FW_SHARED_UI
"""

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from tools import gen_semantics_vectors
from tools.wifi_consts import CONSTS

ROOT = Path(__file__).resolve().parent.parent
SEM_JS = ROOT / "calictl" / "webui" / "semantics.js"
VECTORS = ROOT / "tests" / "vectors" / "semantics.json"

# argv: semantics.js, a JS expression evaluated in its context, optional JSON file bound to __V
_HARNESS = (
    'const fs=require("fs"),vm=require("vm");const ctx=vm.createContext({});'
    'vm.runInContext(fs.readFileSync(process.argv[1],"utf8"),ctx,{filename:"semantics.js"});'
    'if(process.argv[3])ctx.__V=JSON.parse(fs.readFileSync(process.argv[3],"utf8"));'
    "process.stdout.write(JSON.stringify(vm.runInContext(process.argv[2],ctx)));"
)


def node_eval(expr, data_path=None):
    """Evaluate ``expr`` in a fresh context holding semantics.js; returns the JSON-decoded result."""
    node = shutil.which("node") or pytest.skip("node not available")
    args = [node, "-e", _HARNESS, str(SEM_JS), expr] + ([str(data_path)] if data_path else [])
    out = subprocess.run(args, capture_output=True, text=True, check=True, timeout=60)
    return json.loads(out.stdout)


_RUN_VECTORS = """({
  interpret: __V.interpret.map((c) => interpret(c.function, c.fields)),
  states: __V.states.map((c) => {
    const st = {};
    for (const [k, f] of Object.entries(c.fn)) st[k] = interpret(k, f);
    applySwCorrections(st);
    return { state: st, anchors: anchorsCheck(st), firmware: firmwareMeta(st.general) };
  }),
  round: __V.round.map((c) => pyRound(c.x, c.nd)),
  SAT_STALE_S,
})"""


@pytest.fixture(scope="module")
def vectors():
    return json.loads(VECTORS.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def js():
    return node_eval(_RUN_VECTORS, VECTORS)


def test_vectors_are_fresh(vectors):
    assert gen_semantics_vectors.build() == vectors


def test_interpret_matches_python(js, vectors):
    for case, got in zip(vectors["interpret"], js["interpret"], strict=True):
        assert got == case["expect"], case["id"]


def test_whole_states_match_python(js, vectors):
    for case, got in zip(vectors["states"], js["states"], strict=True):
        assert got == case["expect"], case["id"]


def test_py_round_matches_python_round(js, vectors):
    for case, got in zip(vectors["round"], js["round"], strict=True):
        assert got == case["expect"], case


def test_sat_stale_s_is_the_display_stale_threshold(js):
    assert js["SAT_STALE_S"] == CONSTS["DISPLAY_STALE_MS"] / 1000

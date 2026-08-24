"""Guard that docs/api/openapi.yaml stays in sync with the actual routes in calictl/web.py.

The API spec is hand-written (the server is a raw stdlib http.server — nothing to auto-generate),
so this test is what keeps it honest: it fails if a route, the POST allowlist, or the
CONFIRM_REQUIRED set drifts between code and spec. Mirrors the repo's other coverage guardrails
(test_signal_coverage, test_doc_offset_consistency).
"""
import os
import re

import pytest

yaml = pytest.importorskip("yaml")   # PyYAML is a tooling/test dep, not a runtime one

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
WEB_PY = os.path.join(ROOT, "calictl", "web.py")
SPEC = os.path.join(ROOT, "docs", "api", "openapi.yaml")

_API_LITERAL = re.compile(r'"(/api/[a-z_]+)"')


def _web_src():
    with open(WEB_PY, encoding="utf-8") as f:
        return f.read()


def _routes_from_web():
    """(method, path) pairs actually served, parsed from web.py's do_GET / do_POST bodies.

    GET block = between `def do_GET` and `def do_POST`; POST block = from `def do_POST` on. Static
    file serving is the GET fallthrough (not an /api/ literal), so only the JSON API is captured.
    """
    src = _web_src()
    i_get, i_post = src.index("def do_GET"), src.index("def do_POST")
    get_block, post_block = src[i_get:i_post], src[i_post:]
    routes = {("get", p) for p in _API_LITERAL.findall(get_block)}
    routes |= {("post", p) for p in _API_LITERAL.findall(post_block)}
    return routes


def _routes_from_spec():
    with open(SPEC, encoding="utf-8") as f:
        spec = yaml.safe_load(f)
    return {(method, path)
            for path, ops in spec["paths"].items()
            for method in ops if method in ("get", "post", "put", "delete", "patch")}


def test_spec_routes_match_web_routes():
    """Every /api/ route in web.py is documented, and every documented route exists — both ways."""
    web, spec = _routes_from_web(), _routes_from_spec()
    # only compare /api/ paths (the spec omits static file serving by design)
    spec_api = {(m, p) for m, p in spec if p.startswith("/api/")}
    assert web == spec_api, (
        "openapi.yaml drifted from web.py routes.\n"
        "  in web.py but undocumented: %s\n"
        "  documented but not in web.py: %s" % (sorted(web - spec_api), sorted(spec_api - web)))


def test_post_allowlist_matches_spec():
    """web.py's POST allowlist tuple == the POST paths documented in the spec."""
    src = _web_src()
    m = re.search(r'path not in \(([^)]*)\)', src)
    assert m, "could not find the POST allowlist tuple in web.py"
    allow = set(re.findall(r'"(/api/[a-z_]+)"', m.group(1)))
    spec_post = {p for meth, p in _routes_from_spec() if meth == "post"}
    assert allow == spec_post, (
        "POST allowlist vs spec mismatch: web=%s spec=%s" % (sorted(allow), sorted(spec_post)))


def test_confirm_required_matches_spec():
    """web.CONFIRM_REQUIRED == the spec's x-confirm-required list."""
    from calictl import web
    with open(SPEC, encoding="utf-8") as f:
        spec = yaml.safe_load(f)
    spec_confirm = set(spec["info"]["x-confirm-required"])
    assert set(web.CONFIRM_REQUIRED) == spec_confirm, (
        "x-confirm-required %s != web.CONFIRM_REQUIRED %s" % (sorted(spec_confirm),
                                                              sorted(web.CONFIRM_REQUIRED)))


def test_error_tokens_documented():
    """Every error token web.py can emit is listed in the spec's Error enum (no silent tokens)."""
    src = _web_src()
    emitted = set(re.findall(r'"error":\s*"([a-z_]+)"', src))
    with open(SPEC, encoding="utf-8") as f:
        spec = yaml.safe_load(f)
    documented = set(spec["components"]["schemas"]["Error"]["properties"]["error"]["enum"])
    missing = emitted - documented
    assert not missing, "error tokens emitted by web.py but not in the spec Error enum: %s" % sorted(missing)

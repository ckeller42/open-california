import urllib.request
from pathlib import Path

from calictl import web

WEBUI = str(Path(__file__).resolve().parent.parent / "calictl" / "webui")


class _StubBackend:
    read_only = False
    def state(self): return {}
    def screens_bytes(self): return b'{"screens":{},"order":[]}'
    def command(self, *a, **k): return {"ok": True, "applied": True, "state": {}, "error": None}


def _serve():
    srv = web.serve_http(_StubBackend(), WEBUI, "127.0.0.1", 0)
    return srv, "http://127.0.0.1:%d" % srv.server_address[1]


def test_index_and_assets_served():
    srv, base = _serve()
    try:
        for path, needle in (("/", b"<div id=\"app\""), ("/app.js", b"/api/state"),
                             ("/app.css", b"--card")):
            with urllib.request.urlopen(base + path) as r:
                assert r.status == 200
                assert needle in r.read()
    finally:
        srv.shutdown()


def test_no_vw_brand_strings_in_committed_assets():
    # The web UI must ship with neutral wording only — no VW/CaliforniaOnTour
    # branding in the committed frontend files.
    banned = (b"VW California", b"CaliforniaOnTour", b"Volkswagen")
    for name in ("index.html", "app.js", "app.css"):
        content = (Path(WEBUI) / name).read_bytes()
        for needle in banned:
            assert needle not in content, "%s found in %s" % (needle, name)


def test_app_js_surfaces_real_state_keys():
    """Guard the curated data readouts: app.js must reference the interpreted /api/state keys,
    so a regression back to a data-less spec dump is caught."""
    from pathlib import Path
    js = (Path(__file__).resolve().parent.parent / "calictl" / "webui" / "app.js").read_text()
    for key in ("soc2_pct", "batt2_v", "fresh", "waste", "master_on", "usb_charger", "running_time"):
        assert key in js, "app.js no longer surfaces %r" % key

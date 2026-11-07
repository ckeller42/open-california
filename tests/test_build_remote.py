"""The remote app build (``tools/build_remote.py``): the static site published at open-california.github.io.

The contents test runs everywhere; the browser test loads the BUILT page under its own Content-Security-Policy
in Chromium (opt-in like the other e2e tests: skipped without Playwright + Chromium) and fails on a CSP
violation or a page error.

.. test:: The remote build has its CSP, flag, manifest and cache name, and the ESP bundle is not touched
   :id: T_UI_REMOTE_BUILD
   :links: R_UI_REMOTE_APP

.. test:: The built remote app runs the whole LoRa setup (photo scan, QR worker) under its own CSP without a violation
   :id: T_UI_REMOTE_BUILD_SETUP_CSP
   :links: R_UI_REMOTE_APP
"""

import functools
import hashlib
import importlib.util
import json
import re
import subprocess
import sys
import threading
from datetime import UTC, datetime, timedelta
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from tools import build_remote

ROOT = Path(__file__).resolve().parents[1]
BUNDLE = ROOT / "firmware" / "web" / "app_bundle_gen.h"
CSP = (
    "default-src 'self'; connect-src 'self' https://*.thethings.network https://*.thethings.industries; "
    "img-src 'self' data: blob:; worker-src 'self' blob:; script-src 'self'"
)
REMOTE_ONLY = (
    "ttn.js",
    "lora_setup.js",
    "lora_decode.js",
    "remote.js",
    "strings.remote.de.js",
    "vendor/qr-scanner/qr-scanner.umd.min.js",
    "vendor/qr-scanner/qr-scanner-worker.min.js",
    "vendor/qr-scanner/LICENSE",
)


@pytest.fixture(scope="module")
def site(tmp_path_factory):
    out = tmp_path_factory.mktemp("remote") / "site"
    before = hashlib.sha256(BUNDLE.read_bytes()).hexdigest()
    build_remote.build(out)
    assert hashlib.sha256(BUNDLE.read_bytes()).hexdigest() == before, (
        "building the remote app changed the ESP bundle"
    )
    return out


def test_remote_build_contents(site):
    html = (site / "index.html").read_text(encoding="utf-8")
    assert '<meta http-equiv="Content-Security-Policy" content="%s">' % CSP in html
    scripts = re.findall(r"<script([^>]*)>", html)
    assert all(re.fullmatch(r' src="/[^"]+"', s) for s in scripts), (
        "an inline script would break script-src 'self'"
    )
    srcs = [re.search(r'src="/([^"]+)"', s).group(1) for s in scripts]
    assert srcs[0] == "remote-flag.js"
    assert (site / "remote-flag.js").read_text(encoding="utf-8").strip() == "window.OC_REMOTE = true;"
    # the remote scripts after strings.de.js, before app.js, in the e2e harness's order
    assert srcs == [
        "remote-flag.js",
        "strings.de.js",
        "semantics_tables.js",
        "semantics.js",
        "strings.remote.de.js",
        "vendor/qr-scanner/qr-scanner.umd.min.js",
        "lora_setup.js",
        "ttn.js",
        "lora_decode.js",
        "remote.js",
        "app.js",
    ]
    assert "style=" not in html and "<style" not in html
    for name in REMOTE_ONLY:
        assert (site / name).is_file(), name
    assert (site / "vendor" / "qr-scanner" / "README.md").is_file()
    # the page, the manifest and the flag link nothing of the router's API; the service worker refuses it
    for name in ("index.html", "manifest.webmanifest", "remote-flag.js"):
        assert "/api/" not in (site / name).read_text(encoding="utf-8"), name
    sw = (site / "sw.js").read_text(encoding="utf-8")
    assert 'url.pathname.startsWith("/api/")' in sw and "url.origin !== location.origin" in sw
    assert f'const CACHE = "{build_remote.CACHE}"' in sw and "oc-shell-v1" not in sw
    manifest = json.loads((site / "manifest.webmanifest").read_text(encoding="utf-8"))
    assert manifest["name"] == "Open California remote"
    assert manifest["id"] == manifest["start_url"] == manifest["scope"] == "/"
    # the local app's files stay what they were
    local = (ROOT / "calictl" / "webui" / "index.html").read_text(encoding="utf-8")
    assert "OC_REMOTE" not in local and "Content-Security-Policy" not in local
    assert not (site / "jsconfig.json").exists()


def test_only_tracked_files_are_published(tmp_path):
    """An untracked file and a dotfile in calictl/webui never reach the site; every served file is tracked."""
    webui = ROOT / "calictl" / "webui"
    probes = [
        webui / "untracked-probe.txt",
        webui / ".probe-dotfile",
        webui / "vendor" / "untracked-probe.txt",
    ]
    try:
        for p in probes:
            p.write_text("not for the public site", encoding="utf-8")
        build_remote.build(tmp_path / "site")
    finally:
        for p in probes:
            p.unlink(missing_ok=True)
    built = {str(p.relative_to(tmp_path / "site")) for p in (tmp_path / "site").rglob("*") if p.is_file()}
    assert not {"untracked-probe.txt", ".probe-dotfile", "vendor/untracked-probe.txt"} & built
    tracked = set(
        subprocess.run(
            ["git", "ls-files", "calictl/webui"], cwd=ROOT, capture_output=True, text=True, check=True
        ).stdout.split()
    )
    generated = {"remote-flag.js", ".nojekyll"}
    assert {"calictl/webui/" + n for n in built - generated} <= tracked
    assert "lora_decode.js" in built  # generated by gen_lora_dict, and tracked


def test_check_flags_a_stale_or_hand_edited_build(site, tmp_path):
    assert build_remote.main(["--out", str(site), "--check"]) == 0
    stale = tmp_path / "stale"
    build_remote.build(stale)
    (stale / "index.html").write_text("edited", encoding="utf-8")
    (stale / "extra.txt").write_text("x", encoding="utf-8")
    (stale / "app.css").unlink()
    assert build_remote.check(stale) == ["missing: app.css", "extra: extra.txt", "differs: index.html"]
    assert build_remote.main(["--out", str(stale), "--check"]) == 1
    assert build_remote.main(["--out", str(tmp_path / "none"), "--check"]) == 1


# ---- the built page in Chromium, under its CSP -----------------------------------------------------------------

E2E = ROOT / "tests" / "e2e"


def _serve(root, csp_extra=""):
    """A static server for the built site; ``csp_extra`` widens connect-src (the fake TTN is plain http on 127.0.0.1)."""

    class H(SimpleHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_GET(self):
            if self.path.split("?")[0] in ("/", "/index.html"):
                body = (root / "index.html").read_text(encoding="utf-8")
                assert CSP in body
                body = body.replace("connect-src 'self'", "connect-src 'self' " + csp_extra).encode()
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                return
            super().do_GET()

    httpd = ThreadingHTTPServer(("127.0.0.1", 0), functools.partial(H, directory=str(root)))
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return httpd


def test_built_page_loads_under_its_csp_without_violations(site):
    pytest.importorskip("playwright.sync_api")
    # the e2e helper (tests/e2e/conftest.py only provides it to tests/e2e): same gate, loaded by path
    spec = importlib.util.spec_from_file_location("e2e_conftest", E2E / "conftest.py")
    error_gated_page = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(error_gated_page)
    error_gated_page = error_gated_page._error_gated_page
    sys.path.insert(0, str(E2E))
    try:
        from fake_ttn import FakeTtn
        from test_remote_app import CLUSTER, DEV, port2  # also skips here without Chromium
    finally:
        sys.path.remove(str(E2E))
    from playwright.sync_api import expect

    watch = (
        "window.__csp = []; document.addEventListener('securitypolicyviolation', "
        "(e) => window.__csp.push(e.violatedDirective + ' ' + e.blockedURI));"
    )
    with FakeTtn() as ttn:
        _, secret = ttn.make_key("read")
        ttn.add_uplink(DEV, 2, port2(), datetime.now(UTC) - timedelta(minutes=5))
        cfg = {
            "cluster": CLUSTER,
            "appId": ttn.app,
            "devId": DEV,
            "readKey": secret,
            "cmdKey": secret,
            "base": ttn.base,
        }
        # The built page sets OC_REMOTE itself (remote-flag.js): the init script only stores the phone's setup.
        init = watch + "localStorage.setItem('oc.remote.cfg', %s);" % json.dumps(json.dumps(cfg))
        httpd = _serve(site, csp_extra="http://127.0.0.1:*")  # the one test seam, as in the e2e harness
        try:
            url = "http://127.0.0.1:%d/" % httpd.server_address[1]
            with error_gated_page(
                url, init_script=init, allow_errors=("Executing inline script",)
            ) as pg:  # service worker allowed: 127.0.0.1 is secure
                expect(pg.locator("#status")).to_have_text("LoRa · 5 min ago")
                assert pg.evaluate("window.OC_REMOTE") is True
                pg.wait_for_function("navigator.serviceWorker.ready.then(() => true)")
                assert pg.evaluate("window.__csp") == []
                # the policy is really in force: an inline script is refused and reported
                pg.evaluate(
                    "document.head.append(Object.assign(document.createElement('script'), {textContent: 'window.__x = 1'}))"
                )
                assert pg.evaluate("window.__x") is None and len(pg.evaluate("window.__csp")) == 1
        finally:
            httpd.shutdown()
            httpd.server_close()


def test_built_setup_flow_under_its_csp(site):
    """The setup on the BUILT site: paste the key, scan the setup QR as a photo (the vendored qr-scanner and its
    worker), register at the fake TTS, wait for the join, push the keys, take the confirmation. The page carries
    the production CSP; the test server adds only the loopback fake to connect-src. No violation, no page error."""
    pytest.importorskip("playwright.sync_api")
    pytest.importorskip("segno")
    pytest.importorskip("cryptography")
    spec = importlib.util.spec_from_file_location("e2e_conftest", E2E / "conftest.py")
    gate = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(gate)
    from tests.e2e import test_remote_setup as rs  # also skips here without Chromium
    from tests.e2e.fake_ttn import FakeTtn

    watch = (
        "window.__csp = []; document.addEventListener('securitypolicyviolation', "
        "(e) => window.__csp.push(e.violatedDirective + ' ' + e.blockedURI));"
    )
    with FakeTtn() as ttn:
        _, secret = ttn.make_key("all")
        # an empty phone; only the test seam that sends the keys to the fake (built page: OC_REMOTE from its file)
        init = (
            watch
            + "localStorage.getItem('oc.remote.cfg') || localStorage.setItem('oc.remote.cfg', %s);"
            % (json.dumps(json.dumps({"base": ttn.base})))
        )
        httpd = _serve(site, csp_extra="http://127.0.0.1:*")
        try:
            url = "http://127.0.0.1:%d/" % httpd.server_address[1]
            with gate._error_gated_page(url, init_script=init, service_workers="block") as pg:
                pg.clock.install()
                tx = rs.TEXT["en-US"]
                rs._menu(pg, tx["setup"])
                pg.locator("#rs-key").fill(secret)
                pg.get_by_role("button", name=tx["check"]).click()
                pg.get_by_text(tx["scanhint"]).first.wait_for()
                rs._photo(pg, rs.setup_qr())  # QrScanner.scanImage: the worker decodes the photo
                pg.get_by_text(tx["join"]).wait_for()
                frags = [f for _, _, f, _ in rs._join(pg, ttn)]
                rs._confirm(pg, ttn, b"\x00" + hashlib.sha256(rs._plain(frags)).digest()[:4])
                pg.get_by_text(tx["done"]).wait_for()
                assert pg.evaluate("window.OC_REMOTE") is True
                assert pg.evaluate("window.__csp") == []
        finally:
            httpd.shutdown()
            httpd.server_close()

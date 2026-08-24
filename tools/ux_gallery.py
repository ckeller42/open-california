"""Render every web-UI screen against the mock and save PNGs for review.

This is the feedback loop that lets the GUI be reviewed WITHOUT the vehicle: it launches the
real daemon + web UI over the in-process mock (`tools.run_against_mock serve --web`, with the
mock's realistic seeded data), drives headless Chromium, and screenshots the dashboard + every
installed feature screen in light and dark themes.

    pip install playwright && python -m playwright install chromium
    python -m tools.ux_gallery                 # -> /tmp/ux/*.png
    python -m tools.ux_gallery --out ./gallery

Review the PNGs (or send them on) after any GUI change — layout, label, and data-rendering
issues show up here that a headless assertion can't judge. The hard-assertion counterpart is
`tests/e2e/test_gui.py::test_no_red_flag_text`.
"""
from __future__ import annotations

import argparse
import json
import os
import socket
import subprocess
import sys
import time
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
# The Vehicle-tab feature screens, by dashboard-tile title.
SCREENS = ["Cooler", "Camping mode", "Lighting", "Air heater", "Water", "Energy", "Vehicle"]


def _free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def _start_mock_server():
    port = _free_port()
    env = dict(os.environ, CALICTL_ADDR="MO:CK:CA:MP:ER:00", PYTHONUNBUFFERED="1",
               CALICTL_ARM_DELAY_S="0.2", CALICTL_SETTLE_S="0.2", CALICTL_HEARTBEAT_PERIOD_S="0.1",
               CALICTL_HEARTBEAT_WARMUP_S="0", CALICTL_STATE_CACHE="/tmp/calictl_gallery_state.json",
               CALICTL_ENABLE_WRITES="1")   # screenshots show live controls, not the read-only default
    proc = subprocess.Popen(
        [sys.executable, "-m", "tools.run_against_mock", "serve", "--web", str(port),
         "--interval", "1", "--no-influx"],
        cwd=ROOT, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    base = "http://127.0.0.1:%d" % port
    deadline = time.time() + 30
    while time.time() < deadline:
        try:
            state = json.load(urllib.request.urlopen(base + "/api/state", timeout=1))
            if len([k for k, v in state.items() if isinstance(v, dict) and v.get("installed")]) >= 6:
                return proc, base
        except Exception:
            pass
        if proc.poll() is not None:
            raise RuntimeError("mock server exited early")
        time.sleep(0.5)
    proc.terminate()
    raise RuntimeError("mock server did not become ready")


def capture(out_dir, screens=SCREENS):
    """Screenshot the dashboard + each feature screen (light + dark) into out_dir. Returns paths."""
    from playwright.sync_api import sync_playwright  # tool dep; imported lazily
    os.makedirs(out_dir, exist_ok=True)
    proc, base = _start_mock_server()
    paths = []
    try:
        with sync_playwright() as pw:
            for theme in ("light", "dark"):
                browser = pw.chromium.launch()
                ctx = browser.new_context(color_scheme=theme, viewport={"width": 420, "height": 900},
                                          device_scale_factor=2)
                pg = ctx.new_page()
                pg.goto(base)
                pg.wait_for_timeout(1500)
                p = os.path.join(out_dir, "%s_00_dashboard.png" % theme)
                pg.screenshot(path=p, full_page=True)
                paths.append(p)
                for i, name in enumerate(screens, 1):
                    pg.goto(base)
                    pg.wait_for_timeout(400)
                    # click the dashboard TILE (not the "Vehicle" page header, which collides)
                    pg.locator(".tile", has_text=name).first.click()
                    pg.wait_for_timeout(700)
                    p = os.path.join(out_dir, "%s_%02d_%s.png" % (theme, i, name.replace(" ", "")))
                    pg.screenshot(path=p, full_page=True)
                    paths.append(p)
                browser.close()
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except Exception:
            proc.kill()
    return paths


def main(argv=None):
    ap = argparse.ArgumentParser(description="screenshot the web UI (over the mock) for review")
    ap.add_argument("--out", default="/tmp/ux", help="output directory for the PNGs")
    args = ap.parse_args(argv)
    try:
        import playwright  # noqa: F401
    except ImportError:
        raise SystemExit("needs playwright: pip install playwright && python -m playwright install chromium") from None
    paths = capture(args.out)
    print("wrote %d screenshots to %s" % (len(paths), args.out))


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""Measure the satellite UI's first load on the bench (e.g. thinky -> CoreS3 over WiFi).

    python tools/esplab_ui_load.py http://calictl-esp.local/ --runs 5 --shot /tmp/sat-ui.png

Each run opens a fresh browser context (empty cache), loads ``/`` and waits until the page has a
live satellite state (``STATE._meta.satellite``); prints one JSON line with every run's navigation
``loadEventEnd`` and time-to-live-state in ms, their medians and any page/console errors (exit 1 on
errors). Needs Playwright + Chromium (``pip install playwright && python -m playwright install chromium``).
"""

import argparse
import json
import statistics
import sys
import time


def main(argv=None):
    ap = argparse.ArgumentParser(description="satellite UI first-load timing")
    ap.add_argument("url")
    ap.add_argument("--runs", type=int, default=5)
    ap.add_argument("--shot", help="screenshot of the last run's dashboard")
    a = ap.parse_args(argv)
    from playwright.sync_api import sync_playwright  # bench dep; imported lazily

    load, live, errors = [], [], []
    with sync_playwright() as p:
        browser = p.chromium.launch()
        for i in range(a.runs):
            ctx = browser.new_context(viewport={"width": 420, "height": 900})
            pg = ctx.new_page()
            pg.on("pageerror", lambda e: errors.append(str(e)))
            pg.on("console", lambda m: errors.append(m.text) if m.type == "error" else None)
            t0 = time.monotonic()
            pg.goto(a.url, wait_until="load", timeout=30000)
            load.append(pg.evaluate("performance.getEntriesByType('navigation')[0].loadEventEnd"))
            pg.wait_for_function("() => !!(STATE._meta && STATE._meta.satellite)", timeout=30000)
            live.append((time.monotonic() - t0) * 1000)
            if a.shot and i == a.runs - 1:
                pg.screenshot(path=a.shot, full_page=True)
            ctx.close()
        browser.close()
    print(
        json.dumps(
            {
                "runs": a.runs,
                "load_ms": [round(x) for x in load],
                "live_ms": [round(x) for x in live],
                "median_load_ms": round(statistics.median(load)),
                "median_live_ms": round(statistics.median(live)),
                "errors": errors,
            }
        )
    )
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())

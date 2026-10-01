"""Shared e2e helper: a Playwright page whose uncaught JS errors FAIL the test (see test_gui.py's
``page`` fixture for why every e2e test doubles as a runtime-error detector)."""

import contextlib

import pytest


@contextlib.contextmanager
def _error_gated_page(url, **page_kw):
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        browser = p.chromium.launch()
        pg = browser.new_page(**page_kw)
        js_errors = []
        pg.on("pageerror", lambda err: js_errors.append("pageerror: %s" % err))
        pg.on(
            "console",
            lambda msg: (
                js_errors.append("console.error: %s" % msg.text)
                if msg.type == "error" and "favicon" not in msg.text
                else None
            ),
        )
        pg.goto(url)
        try:
            yield pg
        finally:
            browser.close()
        assert not js_errors, "uncaught JS errors during this test:\n  " + "\n  ".join(js_errors)


@pytest.fixture
def error_gated_page():
    """``with error_gated_page(url, locale=...) as page:`` — fails on any pageerror/console.error."""
    return _error_gated_page

"""The firmware page's EN/DE string table (``firmware/web/strings.json``) and its generated outputs.

Every key carries a non-empty ``en`` and ``de``; every key is used by ``firmware/web/index.html`` or
its script ``page.js`` (as a quoted literal) and every ``t("…")`` key the page uses exists; the generated
``strings_gen.h`` + ``index_gen.html`` match a fresh ``tools.gen_c_dict`` regeneration; the rendered
page fits ``NET_HTTP_BODY_MAX`` and loads nothing external.

.. test:: Firmware page strings: EN/DE complete, all used, generated page + header fresh
   :id: T_FW_WEB_STRINGS
   :links: R_FW_HTTP_STATUS
"""

import json
import re
import subprocess
import sys
from pathlib import Path

from tools import gen_c_dict
from tools.wifi_consts import CONSTS

ROOT = Path(__file__).resolve().parents[1]
WEB = ROOT / "firmware" / "web"
STRINGS = json.loads((WEB / "strings.json").read_text(encoding="utf-8"))
# the page source: the markup + its script (page.js, inlined by the generator)
PAGE = (WEB / "index.html").read_text(encoding="utf-8") + (WEB / "page.js").read_text(encoding="utf-8")


def test_every_key_has_en_and_de():
    assert STRINGS
    for key, tr in STRINGS.items():
        assert re.fullmatch(r"[a-z][a-z0-9_]*", key), key
        assert set(tr) == {"en", "de"}, key
        assert tr["en"].strip() and tr["de"].strip(), key


def test_no_key_unused_by_the_page():
    unused = [k for k in STRINGS if '"%s"' % k not in PAGE and "'%s'" % k not in PAGE]
    assert unused == []


def test_every_page_key_exists():
    used = set(re.findall(r"""\bt\(\s*["']([^"']+)["']""", PAGE))
    assert used and used - set(STRINGS) == set()


def test_page_takes_the_poll_interval_from_the_generator():
    assert str(CONSTS["NET_PAGE_POLL_MS"]) not in PAGE
    rendered = gen_c_dict.render_web_page()
    assert not re.search(r"\{\{[A-Z0-9_]+\}\}", rendered)  # no placeholder left (JSDoc {{…}} types are fine)
    assert "pollMs: %d" % CONSTS["NET_PAGE_POLL_MS"] in rendered


def test_rendered_page_is_small_and_self_contained():
    rendered = gen_c_dict.render_web_page().encode()
    assert len(rendered) <= CONSTS["NET_HTTP_BODY_MAX"]
    assert not re.search(rb"""(src|href)\s*=\s*["']?(https?:)?//""", rendered)
    assert b"@import" not in rendered and b'id="setup"' in rendered and b'id="functions"' in rendered


def test_generated_files_are_fresh():
    assert gen_c_dict.generate_web_strings() == (WEB / "strings_gen.h").read_text(encoding="utf-8")
    assert gen_c_dict.render_web_page() == (WEB / "index_gen.html").read_text(encoding="utf-8")
    r = subprocess.run(
        [sys.executable, "-m", "tools.gen_c_dict", "--check"], cwd=ROOT, capture_output=True, text=True
    )
    assert r.returncode == 0, r.stderr


def test_page_script_is_inlined_once():
    rendered = gen_c_dict.render_web_page()
    script = (WEB / "page.js").read_text(encoding="utf-8")
    assert "{{PAGE_JS}}" in PAGE and rendered.count(script) == 1
    assert "const CFG" in rendered.split(script)[0]  # the globals page.js reads come first


def test_header_macros_and_page_bytes():
    text = (WEB / "strings_gen.h").read_text(encoding="utf-8")
    for key, tr in STRINGS.items():
        assert "#define WEB_STR_EN_%s %s" % (key.upper(), json.dumps(tr["en"], ensure_ascii=False)) in text
        assert "#define WEB_STR_DE_%s %s" % (key.upper(), json.dumps(tr["de"], ensure_ascii=False)) in text
    page = (WEB / "index_gen.html").read_bytes()
    assert "#define WEB_INDEX_HTML_LEN %du" % len(page) in text

"""The firmware's on-device display strings (``firmware/web/strings.json``) and the ``/device`` WiFi
recovery page (``firmware/web/index.html``), with their generated outputs.

Every key carries a non-empty ``en`` and ``de`` and is used by the display model (``display_model.c``);
the generated ``strings_gen.h`` + ``index_gen.html`` match a fresh ``tools.gen_c_dict`` regeneration;
the rendered recovery page fits ``NET_HTTP_BODY_MAX``, loads nothing external, carries EN and DE, and
its script parses.

.. test:: Firmware strings + recovery page: EN/DE complete, all used, generated page + header fresh
   :id: T_FW_WEB_STRINGS
   :links: R_FW_HTTP_STATUS
"""

import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from tools import gen_c_dict
from tools.wifi_consts import CONSTS

ROOT = Path(__file__).resolve().parents[1]
WEB = ROOT / "firmware" / "web"
STRINGS = json.loads((WEB / "strings.json").read_text(encoding="utf-8"))
# the on-device display model names its keys as WEB_STR_DE_<KEY> macros (no token pasting)
DISPLAY_SRC = (ROOT / "firmware/components/cali_core/display_model.c").read_text(encoding="utf-8")


def _script(page):
    """The recovery page's one inline script."""
    (script,) = re.findall(r"<script>(.*?)</script>", page, re.S)
    return script


def test_every_key_has_en_and_de():
    assert STRINGS
    for key, tr in STRINGS.items():
        assert re.fullmatch(r"[a-z][a-z0-9_]*", key), key
        assert set(tr) == {"en", "de"}, key
        assert tr["en"].strip() and tr["de"].strip(), key


def test_every_key_is_used_by_the_display():
    assert [k for k in STRINGS if ("WEB_STR_DE_%s" % k.upper()) not in DISPLAY_SRC] == []


def test_recovery_page_is_small_self_contained_and_bilingual():
    page = gen_c_dict.render_web_page()
    assert len(page.encode()) <= CONSTS["NET_HTTP_BODY_MAX"]
    assert not re.search(r"""(src|href)\s*=\s*["']?(https?:)?//""", page)
    assert "@import" not in page and "<!--" not in page
    assert not re.search(r"\{\{[A-Z0-9_]+\}\}", page)  # every constant substituted
    assert '<form id="f">' in page and '<a href="/" id="app">' in page
    script = _script(page)
    assert '"/api/wifi"' in script and "Satellite WiFi" in script and "Satellit-WLAN" in script
    assert "%d" % CONSTS["NET_PSK_MAX"] in script and CONSTS["NET_HOSTNAME"] in script


def test_generated_files_are_fresh():
    assert gen_c_dict.generate_web_strings() == (WEB / "strings_gen.h").read_text(encoding="utf-8")
    assert gen_c_dict.render_web_page() == (WEB / "index_gen.html").read_text(encoding="utf-8")
    r = subprocess.run(
        [sys.executable, "-m", "tools.gen_c_dict", "--check"], cwd=ROOT, capture_output=True, text=True
    )
    assert r.returncode == 0, r.stderr


def test_header_macros_and_page_bytes():
    text = (WEB / "strings_gen.h").read_text(encoding="utf-8")
    for key, tr in STRINGS.items():
        assert "#define WEB_STR_EN_%s %s" % (key.upper(), json.dumps(tr["en"], ensure_ascii=False)) in text
        assert "#define WEB_STR_DE_%s %s" % (key.upper(), json.dumps(tr["de"], ensure_ascii=False)) in text
    page = (WEB / "index_gen.html").read_bytes()
    assert "#define WEB_INDEX_HTML_LEN %du" % len(page) in text


def test_display_format_strings_are_safe():
    # display texts are printf formats (display_model.c row()): a "%d", a lone "%" ("100 %") or a
    # third "%s" in either language is undefined behaviour on the device, and -Wformat can't see it
    for key, tr in STRINGS.items():
        if not key.startswith("d_"):
            continue
        en, de = (re.findall(r"%.?", tr[lang]) for lang in ("en", "de"))
        assert en == de, key
        assert set(en) <= {"%s"} and len(en) <= 2, key
    assert re.findall(r"%.?", STRINGS["d_footer_setup"]["de"]) == ["%s", "%s"]  # SSID + PSK


def test_page_script_stripper():
    js = "/* a\n * b\n */\n// c\n/** @type {X} */ (y);\n/* x */ code();\nkeep();\n"
    assert gen_c_dict.page_script(js) == "/** @type {X} */ (y);\n/* x */ code();\nkeep();\n"
    with pytest.raises(ValueError, match="line 2"):
        gen_c_dict.page_script("/* a\n   more */ init();\n")


@pytest.mark.skipif(not shutil.which("node"), reason="no node")
def test_rendered_page_script_parses(tmp_path):
    f = tmp_path / "recovery.js"
    f.write_text(_script(gen_c_dict.render_web_page()), encoding="utf-8")
    r = subprocess.run(["node", "--check", str(f)], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr

"""The satellite UI bundle: firmware/web/app_bundle_gen.h is the calictl web UI inlined + gzipped.

``tools.gen_c_dict.render_app_bundle`` inlines calictl/webui's index.html + app.css + strings.de.js
+ semantics.js + app.js into one document (no external request: favicon ``data:,``, no manifest or
icons) and ``generate_app_bundle`` gzips it (mtime=0) into a C byte array the firmware serves at
``GET /`` in station mode. Fresh = the array decompresses to today's render (zlib builds may differ
in bytes); it fits ``WEB_APP_GZ_MAX``; an inlined ``</script`` is refused.

.. test:: The satellite UI bundle is fresh, self-contained and within budget
   :id: T_FW_APP_BUNDLE
   :links: R_FW_SHARED_UI
"""

import gzip
import re
import shutil
from pathlib import Path

import pytest

from tools import gen_c_dict
from tools.wifi_consts import CONSTS

ROOT = Path(__file__).resolve().parents[1]
HEADER = ROOT / "firmware" / "web" / "app_bundle_gen.h"
WEBUI = ROOT / "calictl" / "webui"
SOURCES = ("index.html", "app.css", "strings.de.js", "semantics.js", "app.js")


def _gz():
    return gen_c_dict.header_array_bytes(HEADER.read_text(encoding="utf-8"))


def test_header_decompresses_to_a_fresh_render():
    assert gzip.decompress(_gz()) == gen_c_dict.render_app_bundle().encode("utf-8")
    assert gen_c_dict.bundle_is_fresh()


def test_length_macro_matches_the_array():
    assert "#define WEB_APP_HTML_GZ_LEN %du" % len(_gz()) in HEADER.read_text(encoding="utf-8")


def test_gzip_fits_the_budget():
    assert CONSTS["WEB_APP_GZ_MAX"] == 65536
    assert len(_gz()) <= CONSTS["WEB_APP_GZ_MAX"]


def test_bundle_requests_nothing_itself():
    html = gen_c_dict.render_app_bundle()
    assert not re.search(r'<(?:script|link)\b[^>]*\b(?:src|href)="/', html)
    assert not re.search(r'<link rel="(?:manifest|apple-touch-icon|stylesheet)"', html)
    assert html.count('<link rel="icon"') == 1 and '<link rel="icon" href="data:,">' in html
    for name in SOURCES[1:]:
        assert html.count((WEBUI / name).read_text(encoding="utf-8")) == 1, name


def test_no_external_reference_left_in_the_markup():
    # Only the markup outside the inlined <style>/<script> bodies: no tag may point at a URL.
    markup = re.sub(r"<(script|style)>.*?</\1>", "", gen_c_dict.render_app_bundle(), flags=re.S)
    markup = re.sub(r"<!--.*?-->", "", markup, flags=re.S)
    for m in re.finditer(r'<[^>]+\b(?:src|href|action|data|poster)="([^"]*)"', markup):
        assert m.group(1) == "data:,", m.group(0)
    assert "@import" not in gen_c_dict.render_app_bundle()


def _webui_copy(tmp_path):
    d = tmp_path / "webui"
    d.mkdir()
    for name in SOURCES:
        shutil.copy(WEBUI / name, d / name)
    return d


def test_script_order_is_strings_semantics_app():
    html = gen_c_dict.render_app_bundle()
    at = [
        html.index((WEBUI / n).read_text(encoding="utf-8"))
        for n in ("strings.de.js", "semantics.js", "app.js")
    ]
    assert at == sorted(at)


@pytest.mark.parametrize(
    "name,text", [("app.js", "const s = '</script>';\n"), ("app.css", "/* </style> */\n")]
)
def test_an_inlined_closing_tag_is_refused(tmp_path, monkeypatch, name, text):
    d = _webui_copy(tmp_path)
    (d / name).write_text(text, encoding="utf-8")
    monkeypatch.setattr(gen_c_dict, "WEBUI_DIR", d)
    with pytest.raises(ValueError, match=name):
        gen_c_dict.render_app_bundle()


def test_a_missing_script_tag_is_refused(tmp_path, monkeypatch):
    d = _webui_copy(tmp_path)
    (d / "index.html").write_text(
        (WEBUI / "index.html")
        .read_text(encoding="utf-8")
        .replace('<script src="/semantics.js"></script>\n', ""),
        encoding="utf-8",
    )
    monkeypatch.setattr(gen_c_dict, "WEBUI_DIR", d)
    with pytest.raises(ValueError, match="semantics"):
        gen_c_dict.render_app_bundle()


def test_freshness_compares_the_document_not_the_gzip_bytes(tmp_path, monkeypatch):
    hdr = tmp_path / "app_bundle_gen.h"
    monkeypatch.setattr(gen_c_dict, "APP_BUNDLE_OUT", hdr)
    doc = gen_c_dict.render_app_bundle().encode("utf-8")
    hdr.write_text(
        gen_c_dict.app_bundle_header(gzip.compress(doc, compresslevel=1, mtime=0)), encoding="utf-8"
    )
    assert gen_c_dict.bundle_is_fresh()  # other zlib, same document
    hdr.write_text(
        gen_c_dict.app_bundle_header(gzip.compress(doc + b"<!-- edit -->", mtime=0)), encoding="utf-8"
    )
    assert not gen_c_dict.bundle_is_fresh()
    hdr.write_text("garbage", encoding="utf-8")
    assert not gen_c_dict.bundle_is_fresh()

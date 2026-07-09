import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCREENS = ROOT / "calictl" / "webui" / "screens.json"


def test_screens_json_exists_and_matches_build():
    from tools import build_web
    assert SCREENS.exists(), "run: python3 -m tools.build_web"
    on_disk = json.loads(SCREENS.read_text())
    assert on_disk == build_web.build(), "screens.json stale — rebuild with tools.build_web"


def test_pwa_manifest_valid_and_icons_present():
    # The manifest must parse and every icon it references must exist as a real PNG on disk.
    webui = ROOT / "calictl" / "webui"
    man = json.loads((webui / "manifest.webmanifest").read_text())
    assert man["display"] == "standalone" and man["start_url"] == "/"
    for icon in man["icons"]:
        p = webui / icon["src"].lstrip("/")
        assert p.exists(), "manifest references missing icon %s" % icon["src"]
        assert p.read_bytes().startswith(b"\x89PNG"), "%s is not a PNG" % p.name


def test_committed_assets_have_no_vw_app_text():
    # Guard: the committed data must not carry the real VW resource text. Neutral labels
    # never contain a compose key affix, and we deny a few known German app words.
    text = SCREENS.read_text()
    for banned in ("_text", "Kühlbox", "Standheizung", "Aufstelldach"):
        assert banned not in text, "possible VW app content committed: %r" % banned

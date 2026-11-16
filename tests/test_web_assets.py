import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCREENS = ROOT / "calictl" / "webui" / "screens.json"


def test_screens_json_exists_and_matches_build():
    from tools import build_web

    assert SCREENS.exists(), "run: python3 -m tools.build_web"
    on_disk = json.loads(SCREENS.read_text())
    assert on_disk == build_web.build(), "screens.json stale — rebuild with tools.build_web"


def test_screenshot_refs_in_docs_resolve():
    # The CI-rendered shots (light_*/dark_*, esp setup/recovery, ios/) live on the `screenshots`
    # branch and the docs reference them by raw URL; only the hand-made shots are committed. Guard
    # both, so the docs never render a broken image: every RELATIVE `screenshots/` reference points
    # at a committed PNG, and every screenshots raw URL uses the canonical screenshots-branch base.
    import re

    raw_base = "https://raw.githubusercontent.com/ckeller42/open-california/screenshots/docs/screenshots/"
    rel = re.compile(r"(?:\]\(|\{(?:image|figure)\}\s+)(screenshots/[^\s)]+\.png)")
    raw = re.compile(r"https://raw\.githubusercontent\.com/\S+?/docs/screenshots/\S+?\.png")
    sources = [p for p in (ROOT / "docs").rglob("*.md") if "_build" not in p.parts]
    sources.append(ROOT / "README.md")

    missing, bad_base = [], []
    for d in sources:
        text = d.read_text()
        for ref in rel.findall(text):
            p = ROOT / "docs" / ref
            if not (p.exists() and p.read_bytes().startswith(b"\x89PNG")):
                missing.append("%s -> %s" % (d.relative_to(ROOT), ref))
        for url in raw.findall(text):
            if not url.startswith(raw_base):
                bad_base.append("%s -> %s" % (d.relative_to(ROOT), url))
    assert not missing, "relative screenshot refs with no committed PNG: " + "; ".join(missing)
    assert not bad_base, "screenshot raw URLs off the screenshots-branch base: " + "; ".join(bad_base)


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

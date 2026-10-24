"""docs/features.md: generated, reader-facing, and in step with the code it describes."""

import json
import re
from pathlib import Path

import pytest

pytest.importorskip("yaml")

from calictl import control, overrides, protocol  # noqa: E402
from tools import gen_features  # noqa: E402
from tools.gen_c_dict import ESP_CONTROL_FUNCTIONS  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
PAGE = ROOT / "docs" / "features.md"
BANNED = [
    re.compile(r"(?<![\w/&])#\d{2,4}\b"),  # bare issue/PR numbers
    re.compile(r"\bUNVERIFIED\b"),
    re.compile(r"\bMERGED_AMBIGUOUS\b"),
    re.compile(r"\bomit"),
    re.compile(r"\bsurface"),
    re.compile(r"[a-z]{1,2}/[a-z]\.java"),  # obfuscated app class names
    re.compile(r"\b20\d\d-\d\d-\d\d\b"),  # dates
]


def test_features_page_is_fresh_and_reader_clean():
    """The committed page is a fresh regeneration and carries no jargon, history or issue numbers.

    .. test:: docs/features.md is fresh and free of internal jargon
       :id: T_FEATURES_PAGE
       :links: R_FEATURES_PAGE
    """
    text = gen_features.build()
    assert PAGE.read_text() == text, "docs/features.md is stale — run `python3 -m tools.gen_features`"
    for pat in BANNED:
        assert not pat.search(text), "banned token %r in docs/features.md: %r" % (
            pat.pattern,
            pat.search(text)[0],
        )


def test_every_listed_command_builds_and_every_command_is_listed():
    funcs = protocol.load()
    overrides.apply(funcs)
    vectors = json.loads((ROOT / "tests" / "vectors" / "control.json").read_text())
    seed = vectors["states"]["seed"]
    for fn, cmds in gen_features.COMMANDS.items():
        last = {**seed.get(fn, {}), "State": 1} if fn == "cooler" else seed.get(fn, {})
        for what, (_, _, _, sample) in cmds.items():
            frame = control.build(funcs, fn, what, sample, last)
            assert isinstance(frame, bytes), (fn, what)
    built = {
        (c["function"], c["what"])
        for c in vectors["cases"]
        if c["expect"]["kind"] in ("frames", "refused", "elsewhere")
        and not c["what"].startswith("BrightnessL")
    }
    listed = {(fn, w) for fn, cmds in gen_features.COMMANDS.items() for w in cmds}
    assert built <= listed, "commands missing from tools/gen_features.COMMANDS: %s" % sorted(built - listed)
    assert set(control.BUILDERS) == set(gen_features.COMMANDS)


def test_satellite_column_matches_the_c_twin():
    """The satellite's write list in the C header and its command tokens in control.c agree with the page."""
    header = (ROOT / "csrc" / "control_consts.h").read_text()
    c_src = (ROOT / "firmware" / "components" / "cali_core" / "control.c").read_text()
    chars = set(re.findall(r'\{"(\w+)", 0x[0-9a-f]{4}, \d+\}', header))
    assert chars == set(ESP_CONTROL_FUNCTIONS)
    zones = set(re.findall(r'\{"([\w-]+)", "BrightnessL\w+"\}', header))
    assert zones == set(control.LIGHT_ZONES)
    for fn, cmds in gen_features.COMMANDS.items():
        for what in cmds:
            on_sat = gen_features._satellite(fn, what) != "—"
            assert on_sat == (fn in chars and (what in zones or '"%s"' % what in c_src)), (fn, what)
    # the wake-up light: planned from the page's clock, refused without one
    assert re.search(r'strcmp\(what, "wakeup"\) == 0\) \{\s*if \(local_now < 0\)', c_src)


def test_ui_labels_are_the_web_uis_and_name_shown_values():
    """Every "You can see" label is a label string in the web UI, on a value calictl shows."""
    from tools._view_common import load_catalog

    app = (ROOT / "calictl" / "webui" / "app.js").read_text()
    catalog = load_catalog(None)
    for fn, labels in gen_features.UI_LABELS.items():
        shown = {
            e["name"] for e in (catalog[fn].get("state") or {}).values() if e.get("decision") == "surface"
        }
        for name, label in labels.items():
            assert name in shown, (fn, name)
            assert '"%s"' % label in app, "label %r is not in calictl/webui/app.js" % label

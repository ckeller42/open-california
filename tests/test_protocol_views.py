"""Freshness guardrails for docs/protocol/* — generated views of protocol/dictionary.yaml.

Each generator (`tools/gen_*.py`) writes a deterministic Markdown file into `docs/protocol/`.
Nothing re-derives those files automatically, so they can silently drift from the dictionary
they're supposed to describe. Each test here regenerates a view to a tmp path and asserts it's
byte-identical to the committed copy — a stale view fails CI instead of lying quietly.

Add one test per generator/view here (this file is the single home for all of them).
"""
import pathlib

import pytest

pytest.importorskip("yaml")

ROOT = pathlib.Path(__file__).resolve().parent.parent


def test_reference_md_matches_dictionary(tmp_path):
    from tools import gen_protocol_reference

    committed = ROOT / "docs" / "protocol" / "reference.md"
    assert committed.exists(), "run: python3 -m tools.gen_protocol_reference --out docs/protocol/reference.md"

    fresh = gen_protocol_reference.build()
    out = tmp_path / "reference.md"
    out.write_text(fresh)

    assert out.read_text() == committed.read_text(), (
        "docs/protocol/reference.md is stale — regenerate with "
        "`python3 -m tools.gen_protocol_reference --out docs/protocol/reference.md`"
    )


def test_frame_layouts_md_matches_dictionary(tmp_path):
    from tools import gen_frame_layouts

    committed = ROOT / "docs" / "protocol" / "frame-layouts.md"
    assert committed.exists(), (
        "run: python3 -m tools.gen_frame_layouts --out docs/protocol/frame-layouts.md"
    )

    fresh = gen_frame_layouts.build()
    out = tmp_path / "frame-layouts.md"
    out.write_text(fresh)

    assert out.read_text() == committed.read_text(), (
        "docs/protocol/frame-layouts.md is stale — regenerate with "
        "`python3 -m tools.gen_frame_layouts --out docs/protocol/frame-layouts.md`"
    )


def test_wireshark_dissector_matches_dictionary(tmp_path):
    from tools import gen_wireshark_dissector

    committed = ROOT / "docs" / "protocol" / "vwcamper.lua"
    assert committed.exists(), (
        "run: python3 -m tools.gen_wireshark_dissector --out docs/protocol/vwcamper.lua"
    )

    fresh = gen_wireshark_dissector.build()
    out = tmp_path / "vwcamper.lua"
    out.write_text(fresh)

    assert out.read_text() == committed.read_text(), (
        "docs/protocol/vwcamper.lua is stale — regenerate with "
        "`python3 -m tools.gen_wireshark_dissector --out docs/protocol/vwcamper.lua`"
    )


def test_wireshark_dissector_is_non_trivial():
    text = (ROOT / "docs" / "protocol" / "vwcamper.lua").read_text()
    assert "Proto(" in text
    proto_field_count = sum(1 for line in text.splitlines() if "ProtoField." in line)
    assert proto_field_count >= 20, (
        "expected many ProtoField definitions (one per surfaced state field), got %d"
        % proto_field_count
    )


def test_wireshark_dissector_signed_fields(tmp_path):
    """Fields carrying `signed: true` in dictionary.yaml render as ProtoField.intN, not uintN
    (issue #108) — e.g. the vehicle leveling angles and the signed battery currents."""
    from tools import gen_wireshark_dissector
    text = gen_wireshark_dissector.build()
    for ident, ftype in (("carlevelroll", "int16"), ("carlevelpitch", "int16"),
                         ("itwobattbemafs", "int16"), ("idcdcafs", "int16"),
                         ("ionebattbemafs", "int8"), ("pdcdcafs", "int8")):
        line = next((ln for ln in text.splitlines()
                     if "ProtoField." in ln and ident in ln.lower()), None)
        assert line is not None, "no ProtoField line for %s" % ident
        assert "ProtoField.%s(" % ftype in line, (
            "%s should be ProtoField.%s, got: %s" % (ident, ftype, line.strip()))


def test_signal_matrix_md_matches_signals_yaml(tmp_path):
    from tools import gen_signal_matrix

    committed = ROOT / "docs" / "protocol" / "signal-matrix.md"
    assert committed.exists(), (
        "run: python3 -m tools.gen_signal_matrix --out docs/protocol/signal-matrix.md"
    )

    fresh = gen_signal_matrix.build()
    out = tmp_path / "signal-matrix.md"
    out.write_text(fresh)

    assert out.read_text() == committed.read_text(), (
        "docs/protocol/signal-matrix.md is stale — regenerate with "
        "`python3 -m tools.gen_signal_matrix --out docs/protocol/signal-matrix.md`"
    )


# --- independent-correctness checks (issue #107) -------------------------------------------------
# The freshness tests above only regen-diff: a WRONG-but-self-consistent generator passes them
# (exactly how the original mirror-reversed bit grid slipped through its own test). These two
# checks parse the COMMITTED output text and compare it against dictionary.yaml directly — no
# generator code in the loop.

def _dict_functions():
    import yaml
    doc = yaml.safe_load((ROOT / "protocol" / "dictionary.yaml").read_text())
    return doc.get("functions", doc)


def test_reference_rows_match_dictionary_independently():
    """Every placed state/control field appears in its function's reference.md section with
    the exact offset and width cells from dictionary.yaml."""
    text = (ROOT / "docs" / "protocol" / "reference.md").read_text()
    funcs = _dict_functions()
    checked = 0
    for fn, spec in funcs.items():
        start = text.find("\n## %s\n" % fn)
        assert start >= 0, "reference.md: missing section for %s" % fn
        end = text.find("\n## ", start + 1)
        section = text[start:end if end > 0 else len(text)]
        for f in spec.get("state_fields", []) + spec.get("control_fields", []):
            if not (isinstance(f.get("offset"), int) and isinstance(f.get("width"), int)):
                continue
            rows = [ln for ln in section.splitlines()
                    if ln.startswith("| %s |" % f["name"])]
            assert rows, "reference.md[%s]: no row for %s" % (fn, f["name"])
            ok = any(ln.split("|")[2].strip() == str(f["offset"])
                     and ln.split("|")[3].strip() == str(f["width"]) for ln in rows)
            assert ok, "reference.md[%s]: %s row disagrees with dictionary (want off=%s w=%s): %r" % (
                fn, f["name"], f["offset"], f["width"], rows)
            checked += 1
    assert checked > 50, "suspiciously few rows checked (%d)" % checked


def test_frame_layout_grid_matches_dictionary_independently():
    """Every bit of every placed state field sits in the RIGHT grid cell of frame-layouts.md.
    Codec is MSB-first: absolute bit o -> row o//8, column index o%%8 (columns run bit7..bit0).
    This is the check that would have caught the original mirror-reversed grid instantly."""
    text = (ROOT / "docs" / "protocol" / "frame-layouts.md").read_text()
    funcs = _dict_functions()
    checked = 0
    for fn, spec in funcs.items():
        start = text.find("\n## %s\n" % fn)
        assert start >= 0, "frame-layouts.md: missing section for %s" % fn
        end = text.find("\n## ", start + 1)
        section = text[start:end if end > 0 else len(text)]
        st = section.find("### State frame")
        if st < 0:
            continue
        st_end = section.find("### ", st + 4)
        state_block = section[st:st_end if st_end > 0 else len(section)]
        # parse the grid: rows "| <byte> | c7 | c6 | ... | c0 |"
        grid = {}
        for ln in state_block.splitlines():
            cells = [c.strip() for c in ln.strip().strip("|").split("|")]
            if len(cells) == 9 and cells[0].isdigit():
                grid[int(cells[0])] = cells[1:]
        if not grid:
            continue
        for f in spec.get("state_fields", []):
            if not (isinstance(f.get("offset"), int) and isinstance(f.get("width"), int)):
                continue
            for k in range(f["width"]):
                o = f["offset"] + k
                row, col = o // 8, o % 8
                assert row in grid, "frame-layouts.md[%s]: no grid row for byte %d" % (fn, row)
                assert grid[row][col] == f["name"], (
                    "frame-layouts.md[%s]: byte %d bit-col %d should be %s, grid says %r"
                    % (fn, row, 7 - col, f["name"], grid[row][col]))
                checked += 1
    assert checked > 100, "suspiciously few grid cells checked (%d)" % checked

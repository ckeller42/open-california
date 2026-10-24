"""Tests for calictl.cli's status rendering helpers (_fmt / _render_status):
the `calictl status` output contract (bool yes/no, not-installed lines,
nested dict rendering, raw-byte suppression)."""

from calictl import cli


def test_fmt_renders_bools_as_yes_no():
    assert cli._fmt(True) == "yes"
    assert cli._fmt(False) == "no"


def test_fmt_passes_other_values_through():
    assert cli._fmt(42) == 42
    assert cli._fmt("error") == "error"
    assert cli._fmt(None) is None


def test_render_status_not_installed_line():
    line = cli._render_status("roof", {"installed": False, "on": True})
    assert "not installed" in line
    # the not-installed branch returns early: no field list is rendered
    assert "on=" not in line


def test_render_status_renders_fields_and_nested_dicts():
    line = cli._render_status(
        "water",
        {
            "fresh": {"percent": 80, "alert": "empty"},
            "state": True,
            "raw": b"\x00\x01",
        },
    )
    assert "fresh=percent=80 alert=empty" in line
    assert "state=yes" in line


def test_render_status_skips_raw_bytes():
    # the codec's raw frame is never human-facing; it must not leak into the status line
    line = cli._render_status("cooler", {"on": True, "raw": b"\x00"})
    assert "raw" not in line


def test_cli_help_lists_every_lighting_zone():
    """The module help's ``set lighting <zone>`` line names every ``control.LIGHT_ZONES`` key."""
    from calictl import control

    line = next(ln for ln in cli.__doc__.splitlines() if "set lighting <zone>" in ln)
    missing = [z for z in control.LIGHT_ZONES if z not in line and not z.startswith("reading-")]
    assert not missing, missing

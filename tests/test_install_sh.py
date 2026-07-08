"""Tests for the guided Raspberry Pi installer (install.sh).

No radio, no apt, no sudo: syntax + the pure bluetoothctl-output parser (sourced via the
OC_INSTALL_LIB library guard) + a --dry-run walkthrough that must preview every step and
touch nothing. shellcheck runs in CI; this covers behaviour a linter can't.
"""
import os
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SH = str(ROOT / "install.sh")


def _lib(snippet: str):
    """Source install.sh as a library (main() suppressed) and run `snippet`."""
    env = {**os.environ, "OC_INSTALL_LIB": "1"}
    return subprocess.run(["sh", "-c", '. "%s"; %s' % (SH, snippet)],
                          capture_output=True, text=True, env=env)


def test_syntax_ok():
    r = subprocess.run(["sh", "-n", SH], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr


def test_help_lists_flags():
    r = subprocess.run(["sh", SH, "--help"], capture_output=True, text=True)
    assert r.returncode == 0 and "--with-sinks" in r.stdout and "--dry-run" in r.stdout


def test_parse_device_mac_standard_line():
    r = _lib('printf "Device A1:B2:C3:D4:E5:F6 VWCAMPER\\nDevice AA:BB:CC:DD:EE:FF Other\\n" | parse_device_mac')
    assert r.stdout.strip() == "A1:B2:C3:D4:E5:F6"


def test_parse_device_mac_new_scan_line():
    r = _lib('printf "[NEW] Device AA:BB:CC:DD:EE:FF VWCAMPER\\n" | parse_device_mac')
    assert r.stdout.strip() == "AA:BB:CC:DD:EE:FF"


def test_parse_device_mac_absent_is_empty():
    r = _lib('printf "Device 11:22:33:44:55:66 SomethingElse\\n" | parse_device_mac')
    assert r.stdout.strip() == ""


def test_dry_run_previews_all_steps_and_touches_nothing(tmp_path):
    target = tmp_path / "oc"
    r = subprocess.run(["sh", SH, "--dry-run", "--yes", "--dir", str(target)],
                       capture_output=True, text=True, cwd=str(tmp_path))
    assert r.returncode == 0, r.stderr
    out = r.stdout
    for marker in ("DRY RUN", "apt-get install", "pip install bleak", "pairing",
                   "calictl.env", "calictl status", "systemd", "Done."):
        assert marker in out, "missing %r in dry-run output" % marker
    assert not target.exists()            # dry-run created nothing


def test_with_sinks_dry_run_configures_mqtt_without_leaking_password(tmp_path):
    r = subprocess.run(["sh", SH, "--dry-run", "--yes", "--with-sinks", "--dir", str(tmp_path / "oc")],
                       capture_output=True, text=True, cwd=str(tmp_path))
    assert r.returncode == 0, r.stderr
    out = r.stdout
    assert "MQTT" in out and "paho-mqtt" in out          # sink deps + MQTT config previewed
    assert "password hidden" in out                       # password is never printed

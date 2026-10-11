"""cali_ota: the firmware-update state machine against a fake fetcher and slot.

``firmware/components/cali_core/ota.c`` driven through ``test/ota_cli.c``: the manifest's sha vs the
running build, the SHA-256 of what landed vs the manifest, and that the boot partition is switched
(``commit``) only after both pass. The HTTPS/flash side (``components/cali_ota``) is ESP-only.

.. test:: OTA state machine: up to date, available, install, checksum mismatch never switches the slot
   :id: T_FW_OTA_SM
   :links: R_FW_OTA
"""

import hashlib
import json
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
CORE = ROOT / "firmware" / "components" / "cali_core"
SHA = "0123456789abcdef0123456789abcdef01234567"
IMAGE = b"cali_fw image bytes"
DIGEST = hashlib.sha256(IMAGE).hexdigest()
SIZE = len(IMAGE)
BASE = "https://github.com/ckeller42/open-california/releases/download/esp-latest/"


def manifest(sha=SHA, size=SIZE, sha256=DIGEST):
    return json.dumps(
        {
            "sha": sha,
            "size": size,
            "sha256": sha256,
            "run_id": 38127176445,
            "built_at": "2026-10-11T10:00:00Z",
        }
    )


@pytest.fixture(scope="module")
def ota_cli(tmp_path_factory):
    cc = shutil.which("cc") or pytest.skip("no C compiler")
    out = tmp_path_factory.mktemp("ota") / "ota_cli"
    subprocess.run(
        [
            cc,
            "-std=c99",
            "-Wall",
            "-Wextra",
            "-Werror",
            "-I",
            str(CORE / "include"),
            "-I",
            str(ROOT / "firmware" / "components" / "platform" / "include"),
            str(CORE / "ota.c"),
            str(CORE / "json.c"),
            str(CORE / "test" / "ota_cli.c"),
            "-o",
            str(out),
        ],
        check=True,
    )
    return out


def run(cli, *lines):
    out = subprocess.run(
        [str(cli)], input="\n".join(lines) + "\n", capture_output=True, text=True, check=True
    )
    return out.stdout.splitlines()


def calls(out):
    return [ln[5:] for ln in out if ln.startswith("CALL ")]


def otas(out):
    """Every ``json`` line's device.ota object, in order."""
    return [json.loads(ln[5:])["ota"] for ln in out if ln.startswith("JSON ")]


def ota(out):
    return otas(out)[-1]


def test_idle_before_any_request(ota_cli):
    assert ota(run(ota_cli, "json")) == {
        "state": "idle",
        "available_sha": None,
        "progress_pct": 0,
        "error": None,
    }


@pytest.mark.parametrize("fw", ["0123456", SHA[:12], SHA])
def test_same_sha_is_up_to_date(ota_cli, fw):
    out = run(ota_cli, "fw " + fw, "manifest " + manifest(), "install", "json", "work", "json")
    assert "REQ 0" in out
    assert ota(out)["state"] == "up_to_date"
    assert calls(out) == [
        "wake",
        "get " + BASE + "manifest.json?t=2000",
    ]  # install of the running build: no download


@pytest.mark.parametrize("fw", ["fedcba9", "0123456-dirty", "0123", "unknown"])
def test_other_build_sees_available_and_check_downloads_nothing(ota_cli, fw):
    out = run(ota_cli, "fw " + fw, "manifest " + manifest(), "check", "json", "work", "json")
    assert otas(out)[0] == {"state": "checking", "available_sha": None, "progress_pct": 0, "error": None}
    assert ota(out) == {"state": "available", "available_sha": SHA, "progress_pct": 0, "error": None}
    assert calls(out) == ["wake", "get " + BASE + "manifest.json?t=2000"]


def test_every_check_fetches_a_fresh_manifest_url(ota_cli):
    """A cache-buster per check (GitHub's edge once served a stale manifest for ~2 min); the image
    URL carries none (it is SHA-256 checked)."""
    out = run(ota_cli, "manifest " + manifest(), "check", "work", "check", "work")
    gets = [c for c in calls(out) if c.startswith("get ")]
    assert len(gets) == 2 and gets[0] != gets[1]
    assert all(g.startswith("get " + BASE + "manifest.json?t=") for g in gets)


def test_install_downloads_verifies_then_switches_and_restarts(ota_cli):
    out = run(ota_cli, "manifest " + manifest(), "slot " + DIGEST, "install", "work", "json")
    assert calls(out) == [
        "wake",
        "get " + BASE + "manifest.json?t=2000",
        "download %scali_fw.bin %d (downloading)" % (BASE, len(IMAGE)),  # never "available" in between
        "slot_sha256 %d" % len(IMAGE),
        "commit",
        "restart",
    ]
    assert ota(out) == {"state": "rebooting", "available_sha": SHA, "progress_pct": 100, "error": None}


@pytest.mark.parametrize("slot", ["00" * 32, "fail"])
def test_checksum_mismatch_is_an_error_and_never_switches_the_slot(ota_cli, slot):
    out = run(ota_cli, "manifest " + manifest(), "slot " + slot, "install", "work", "json")
    assert calls(out)[-2:] == ["slot_sha256 %d" % len(IMAGE), "abort"]
    assert "commit" not in calls(out) and "restart" not in calls(out)
    assert ota(out) == {"state": "error", "available_sha": SHA, "progress_pct": 100, "error": "checksum"}


def test_download_failure_aborts_without_switching(ota_cli):
    out = run(ota_cli, "manifest " + manifest(), "download fail", "install", "work", "json")
    assert calls(out)[-2:] == ["download %scali_fw.bin %d (downloading)" % (BASE, len(IMAGE)), "abort"]
    assert ota(out)["error"] == "download" and 0 < ota(out)["progress_pct"] < 100  # half way


def test_commit_failure_is_an_error_without_restart(ota_cli):
    out = run(ota_cli, "manifest " + manifest(), "slot " + DIGEST, "commit fail", "install", "work", "json")
    assert calls(out)[-1] == "commit"
    assert ota(out)["state"] == "error" and ota(out)["error"] == "commit"


@pytest.mark.parametrize(
    "body",
    [
        "-",  # the GET failed
        "not json",
        manifest(sha="0123456"),  # not 40 hex
        manifest(sha=SHA.upper()),
        manifest(sha256="abc"),
        manifest(size=0),
        manifest(size=0x300001),  # bigger than the slot
        json.dumps({"sha": SHA, "sha256": DIGEST}),  # no size
    ],
)
def test_bad_manifest_is_an_error(ota_cli, body):
    out = run(ota_cli, "manifest " + body, "install", "work", "json")
    assert ota(out) == {"state": "error", "available_sha": None, "progress_pct": 0, "error": "manifest"}
    assert not any(c.startswith(("download", "commit")) for c in calls(out))


def test_one_request_at_a_time_then_again_after_it_ends(ota_cli):
    out = run(ota_cli, "manifest " + manifest(), "check", "install", "work", "check", "json")
    assert [ln for ln in out if ln.startswith("REQ ")] == ["REQ 0", "REQ -2", "REQ 0"]
    assert ota(out)["state"] == "checking" and ota(out)["available_sha"] is None  # a new check clears it


def test_no_ota_build_refuses(ota_cli):
    out = run(ota_cli, "noinit", "check", "work")
    assert out[0] == "REQ -1" and calls(out) == []

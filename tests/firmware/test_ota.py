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
BASE = "https://github.com/ckeller42/open-california/releases/download/esp-stable/"  # the default channel


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
            str(CORE / "diag.c"),
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
        "channel": "stable",
        "verify": "valid",
    }


@pytest.mark.parametrize("fw", ["0123456", SHA[:12], SHA])
def test_same_sha_is_up_to_date(ota_cli, fw):
    out = run(ota_cli, "fw " + fw, "manifest " + manifest(), "install", "json", "work", "json")
    assert "REQ 0" in out
    assert ota(out)["state"] == "up_to_date"
    assert calls(out) == [
        "wake",
        "get " + BASE + "manifest.json?t=4000",  # fake clock: +1 s per read (2 by the ota diag event)
    ]  # install of the running build: no download


@pytest.mark.parametrize("fw", ["fedcba9", "0123456-dirty", "0123", "unknown"])
def test_other_build_sees_available_and_check_downloads_nothing(ota_cli, fw):
    out = run(ota_cli, "fw " + fw, "manifest " + manifest(), "check", "json", "work", "json")
    assert otas(out)[0] == {
        "state": "checking",
        "available_sha": None,
        "progress_pct": 0,
        "error": None,
        "channel": "stable",
        "verify": "valid",
    }
    assert ota(out) == {
        "state": "available",
        "available_sha": SHA,
        "progress_pct": 0,
        "error": None,
        "channel": "stable",
        "verify": "valid",
    }
    assert calls(out) == ["wake", "get " + BASE + "manifest.json?t=4000"]  # fake clock: +1 s per read


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
        "get " + BASE + "manifest.json?t=4000",  # fake clock: +1 s per read (2 by the ota diag event)
        "download %scali_fw.bin %d (downloading)" % (BASE, len(IMAGE)),  # never "available" in between
        "slot_sha256 %d" % len(IMAGE),
        "commit",
        "restart",
    ]
    assert ota(out) == {
        "state": "rebooting",
        "available_sha": SHA,
        "progress_pct": 100,
        "error": None,
        "channel": "stable",
        "verify": "valid",
    }


@pytest.mark.parametrize("slot", ["00" * 32, "fail"])
def test_checksum_mismatch_is_an_error_and_never_switches_the_slot(ota_cli, slot):
    out = run(ota_cli, "manifest " + manifest(), "slot " + slot, "install", "work", "json")
    assert calls(out)[-2:] == ["slot_sha256 %d" % len(IMAGE), "abort"]
    assert "commit" not in calls(out) and "restart" not in calls(out)
    assert ota(out) == {
        "state": "error",
        "available_sha": SHA,
        "progress_pct": 100,
        "error": "checksum",
        "channel": "stable",
        "verify": "valid",
    }


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
    assert ota(out) == {
        "state": "error",
        "available_sha": None,
        "progress_pct": 0,
        "error": "manifest",
        "channel": "stable",
        "verify": "valid",
    }
    assert not any(c.startswith(("download", "commit")) for c in calls(out))


def test_check_uses_the_channel_release(ota_cli):
    """The default channel is stable; a stored ``latest`` switches the manifest URL.

    .. test:: The OTA check fetches the channel's release
       :id: T_FW_OTA_CHANNEL
       :links: R_FW_OTA
    """
    out = run(ota_cli, "manifest " + manifest(), "check", "work")  # default channel
    assert any(
        ln.startswith(
            "CALL get https://github.com/ckeller42/open-california/releases/download/esp-stable/manifest.json?t="
        )
        for ln in out
    )
    out = run(ota_cli, "channel latest", "manifest " + manifest(), "check", "work")
    assert any("/releases/download/esp-latest/manifest.json?t=" in ln for ln in out)
    assert any(ln.startswith("CALL get") for ln in out)


def test_install_downloads_from_the_same_channel(ota_cli):
    out = run(ota_cli, "channel latest", "manifest " + manifest(), "install", "work")
    assert any(
        ln.startswith(
            "CALL download https://github.com/ckeller42/open-california/releases/download/esp-latest/cali_fw.bin"
        )
        for ln in out
    )


def test_channel_json(ota_cli):
    assert '"channel":"stable"' in run(ota_cli, "json")[-1]
    assert '"channel":"latest"' in run(ota_cli, "channel latest", "json")[-1]


def test_channel_change_clears_a_stale_available(ota_cli):
    """A settled result belongs to the old channel: switching drops it, so no stale Install is offered."""
    out = run(ota_cli, "manifest " + manifest(), "check", "work", "json")
    assert ota(out)["state"] == "available" and ota(out)["available_sha"]
    o = ota(run(ota_cli, "manifest " + manifest(), "check", "work", "channel latest", "json"))
    assert o["state"] == "idle" and o["available_sha"] is None and o["channel"] == "latest"


def test_one_request_at_a_time_then_again_after_it_ends(ota_cli):
    out = run(ota_cli, "manifest " + manifest(), "check", "install", "work", "check", "json")
    assert [ln for ln in out if ln.startswith("REQ ")] == ["REQ 0", "REQ -2", "REQ 0"]
    assert ota(out)["state"] == "checking" and ota(out)["available_sha"] is None  # a new check clears it


def test_no_ota_build_refuses(ota_cli):
    out = run(ota_cli, "noinit", "check", "work")
    assert out[0] == "REQ -1" and calls(out) == []


def test_every_transition_is_an_ota_event(ota_cli):
    """R_FW_DIAG_LOG: check -> downloading -> verifying -> rebooting (or error + its code), each with
    the time since the request (the fake clock: +1 s per read).

    .. test:: Every OTA state transition is a diagnostics event
       :id: T_FW_DIAG_OTA
       :links: R_FW_DIAG_LOG
    """
    import json

    def evts(*lines):
        out = run(ota_cli, "diaghook", "manifest " + manifest(), *lines)
        return [json.loads(ln[5:]) for ln in out if ln.startswith("DIAG ")]

    ok = evts("slot " + DIGEST, "install", "work")
    assert [e["state"] for e in ok] == ["checking", "downloading", "verifying", "rebooting"]
    assert all(e["kind"] == "ota" and "error" not in e for e in ok) and ok[-1]["dur_ms"] > ok[0]["dur_ms"]
    bad = evts("install", "work")  # slot digest all zero: checksum
    assert bad[-1] | {"dur_ms": 0} == {
        "ev": "evt",
        "kind": "ota",
        "state": "error",
        "dur_ms": 0,
        "error": "checksum",
    }


# ---- the verdict on a freshly installed image (cali_ota_verify_*) ----
MIN, T, W, GONE = 60000, 15 * 60000, 40 * 60000, 5 * 60000  # the shipped CALI_OTA_VERIFY_* values
NO = -1  # adv_ago: never seen


def step(now, web=1, link=0, ago=NO, bonded=1, off=0, synced=1):
    return "vstep %d %d %d %d %d %d %d" % (now, web, link, ago, bonded, off, synced)


def verdicts(out):
    return [ln.split()[1] for ln in out if ln.startswith("VERDICT ")]


def test_verify_a_confirmed_image_needs_no_verdict(ota_cli):
    out = run(ota_cli, "vbegin 0 0 %d %d %d %d" % (MIN, T, W, GONE), step(0, web=0), "json")
    assert verdicts(out) == ["valid"] and ota(out)["verify"] == "valid"


def test_verify_a_link_once_is_valid_after_the_minimum(ota_cli):
    """The link counts from boot on and is latched (a rest probe's link lasts seconds); the verdict
    still waits for MIN and the web server.

    .. test:: OTA verdict: a camper link once confirms the image; nothing before 60 s or without the web server
       :id: T_FW_OTA_VERIFY_LINK
       :links: R_FW_OTA
    """
    out = run(
        ota_cli,
        "diaghook",
        "vbegin 1 1000 %d %d %d %d" % (MIN, T, W, GONE),
        step(21000, link=1),  # up at 20 s of the run ...
        step(31000),  # ... and down again
        step(MIN + 999),  # 59.999 s since the begin
        step(MIN + 1000, web=0),
        step(MIN + 1000),
        step(MIN + 2000, ago=0),  # a verdict stays
        "json",
        "vconfirm",
        "json",
    )
    assert verdicts(out) == ["pending"] * 4 + ["valid", "valid"]
    # device.ota.verify reads valid only once the platform marked the slot valid
    assert [o["verify"] for o in otas(out)] == ["pending", "valid"]
    assert any('"verdict":"valid","why":"link","t_ms":60000' in ln for ln in out if ln.startswith("DIAG "))


def test_verify_no_unit_is_valid_at_t(ota_cli):
    """The camper unit off or the van parked alone: no advert, so the image is valid at T — the
    absence of the unit is no fault.

    .. test:: OTA verdict: no unit advert by 15 min confirms a healthy image (absence is no fault)
       :id: T_FW_OTA_VERIFY_NO_UNIT
       :links: R_FW_OTA
    """
    out = run(ota_cli, "vbegin 1 0 %d %d %d %d" % (MIN, T, W, GONE), step(MIN), step(T - 1), step(T), "json")
    assert verdicts(out) == ["pending", "pending", "valid"]
    assert "LOG ota: image valid (no_unit) after 900 s" in out


def test_verify_a_ble_host_that_never_synced_fails_at_t(ota_cli):
    """A dead BLE stack sees no advert either: without this leg it would read as "no unit" and pass.
    Bond or not — the host syncs with its controller without one.

    .. test:: OTA verdict: a BLE host that never synced fails the image at 15 min
       :id: T_FW_OTA_VERIFY_BLE_DEAD
       :links: R_FW_OTA
    """
    for bonded in (1, 0):
        out = run(
            ota_cli,
            "vbegin 1 0 %d %d %d %d" % (MIN, T, W, GONE),
            step(T - 1, bonded=bonded, synced=0),
            step(T, bonded=bonded, synced=0),
            step(T + 1, link=1),
            "json",
        )
        assert verdicts(out) == ["pending", "failed", "failed"], bonded
        assert ota(out)["verify"] == "failed"
        assert "LOG ota: image failed (ble_dead) after 900 s" in out
    out = run(ota_cli, "vbegin 1 0 %d %d %d %d" % (MIN, T, W, GONE), step(1000, synced=1), step(T, synced=0))
    assert verdicts(out) == ["pending", "valid"]  # synced once is enough (latched)


def test_verify_an_advertising_unit_without_a_link_fails_after_w(ota_cli):
    """The failure signature: the bonded unit keeps advertising, the BLE mode is not off, and no link
    comes up for W after the first advert. T passing in between decides nothing.

    .. test:: OTA verdict: the unit advertises but no link for 40 min fails the image
       :id: T_FW_OTA_VERIFY_NO_LINK
       :links: R_FW_OTA
    """
    first = 5 * 60000
    lines = ["vbegin 1 0 %d %d %d %d" % (MIN, T, W, GONE)]
    lines += [step(m * 60000, ago=20000) for m in range(5, 45)]  # adverts from minute 5, past T
    lines += [step(first + W - 1, ago=20000), step(first + W, ago=20000), step(first + W + 1, link=1), "json"]
    out = run(ota_cli, *lines)
    assert verdicts(out) == ["pending"] * 41 + ["failed", "failed"]  # a later link changes nothing
    assert "LOG ota: image failed (no_link) after 2700 s" in out
    assert ota(out)["verify"] == "failed"


def test_verify_the_rest_poll_link_inside_w_is_valid(ota_cli):
    """Camping off: the session leaves an advertising unit alone and connects only on its 30 min rest
    poll. That brief link inside W confirms the image."""
    lines = ["vbegin 1 0 %d %d %d %d" % (MIN, T, W, GONE)]
    lines += [step(m * 60000, ago=15000) for m in range(1, 31)]
    lines += [step(31 * 60000, ago=15000, link=1)]
    assert verdicts(run(ota_cli, *lines)) == ["pending"] * 30 + ["valid"]


@pytest.mark.parametrize(
    "case, kw",
    [
        ("asleep", {"ago": GONE}),  # the last advert 5 min old: the unit went back to sleep
        ("ble_off", {"ago": 1000, "off": 1}),  # off: no link on purpose
        ("no_bond", {"ago": 1000, "bonded": 0}),  # nothing to connect to
    ],
)
def test_verify_no_window_is_valid_at_t(ota_cli, case, kw):
    """An advert that stopped, the BLE mode off or no bond opens no window: valid at T."""
    out = run(ota_cli, "vbegin 1 0 %d %d %d %d" % (MIN, T, W, GONE), step(T - 1, **kw), step(T, **kw))
    assert verdicts(out) == ["pending", "valid"], case


def test_verify_a_sleeping_unit_restarts_the_window(ota_cli):
    """A door opened (adverts), then shut (no advert for 5 min): the window ends; a later advert starts
    a new one, so 40 min counts from there, not from the first advert."""
    lines = ["vbegin 1 0 %d %d %d %d" % (MIN, T, W, GONE)]
    lines += [step(m * 60000, ago=1000) for m in range(1, 4)]  # minutes 1-3 advertising
    lines += [step(9 * 60000, ago=6 * 60000)]  # asleep: window ends
    lines += [step(m * 60000, ago=1000) for m in range(20, 60)]  # a new window from minute 20
    lines += [step(60 * 60000, ago=1000)]
    v = verdicts(run(ota_cli, *lines))
    assert set(v[:-1]) == {"pending"} and v[-1] == "failed"

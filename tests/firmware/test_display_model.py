"""Status display model: rows, stale rule, footer, brightness (#154).

.. test:: Display model maps every status to the right row colour, text, footer and brightness
   :id: T_FW_DISPLAY_MODEL
   :links: R_FW_STATUS_DISPLAY
"""

import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
CORE = ROOT / "firmware/components/cali_core"


@pytest.fixture(scope="module")
def cli(tmp_path_factory):
    cc = shutil.which("cc") or pytest.skip("no C compiler")
    out = tmp_path_factory.mktemp("dm") / "display_cli"
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
            str(ROOT / "csrc"),
            "-I",
            str(ROOT / "firmware/web"),
            str(CORE / "display_model.c"),
            str(CORE / "test/display_cli.c"),
            "-o",
            str(out),
        ],
        check=True,
    )
    return out


def run(cli, *lines):
    out = (
        subprocess.run([str(cli)], input="\n".join(lines) + "\n", capture_output=True, text=True, check=True)
        .stdout.strip()
        .splitlines()
    )
    return [dict(f.split("=", 1) for f in line.split(" ;; ")) for line in out]


def run_raw(cli, *lines):
    return (
        subprocess.run([str(cli)], input="\n".join(lines) + "\n", capture_output=True, text=True, check=True)
        .stdout.strip()
        .splitlines()
    )


BASE = (
    "pair=6 addr=AA:BB:CC:DD:EE:FF link=1 age=800 wmode=2 wstate=3 ssid=Insel "
    "ip=3232281174 rssi=-58 fail= up=7980000"
)  # 192.168.178.86, 2 h 13 min


def test_all_green_online(cli):
    (v,) = run(cli, BASE + " now=7980000 lang=0")
    assert v["device"] == "green|läuft · seit 2 h 13 min"
    assert v["wifi"] == "green|Insel · 192.168.178.86 · -58 dBm"
    assert v["camper"] == "green|verbunden · Daten vor 1 s"
    assert v["footer"] == "0"


def test_camper_link_up_no_data_yet(cli):  # Review Focus 2
    (v,) = run(cli, BASE.replace("age=800", "age=-1") + " now=1000 lang=0")
    assert v["camper"].startswith("amber|")


def test_camper_stale_after_10s(cli):
    (v,) = run(cli, BASE.replace("age=800", "age=10001") + " now=20000 lang=0")
    assert v["camper"] == "red|keine Daten seit 10 s"


def test_camper_link_lost(cli):
    (v,) = run(cli, BASE.replace("link=1", "link=0") + " now=20000 lang=0")
    assert v["camper"] == "red|Verbindung verloren, verbinde neu"


def test_camper_not_paired(cli):
    (v,) = run(
        cli, BASE.replace("pair=6 addr=AA:BB:CC:DD:EE:FF link=1", "pair=0 addr= link=0") + " now=20000 lang=0"
    )
    assert v["camper"] == "grey|nicht gekoppelt"


@pytest.mark.parametrize(
    "pair,word",
    [(1, "verbinde"), (2, "verbinde"), (4, "Kopplung"), (3, "Code"), (5, "Kopplung"), (8, "verbinde")],
)
def test_camper_pairing_in_progress_is_amber(cli, pair, word):
    (v,) = run(
        cli, BASE.replace("pair=6", "pair=%d" % pair).replace("link=1", "link=0") + " now=20000 lang=0"
    )
    assert v["camper"].startswith("amber|") and word in v["camper"]


def test_camper_pairing_error(cli):
    (v,) = run(cli, BASE.replace("pair=6", "pair=7").replace("link=1", "link=0") + " now=1 lang=0")
    assert v["camper"] == "red|Kopplung fehlgeschlagen"


def test_wifi_setup_mode_and_footer(cli):
    (v,) = run(
        cli,
        BASE.replace("wmode=2 wstate=3", "wmode=1 wstate=1")
        .replace("ssid=Insel", "ssid=")
        .replace("ip=3232281174", "ip=0")
        + " now=1 lang=0",
    )
    assert v["wifi"] == "amber|Einrichtungs-Hotspot calictl-esp-setup · 192.168.4.1"
    assert v["footer"] == "1"


def test_wifi_off_with_reason(cli):
    (v,) = run(
        cli,
        BASE.replace("wmode=2 wstate=3", "wmode=0 wstate=0")
        .replace("fail=", "fail=auth")
        .replace("ip=3232281174", "ip=0")
        + " now=1 lang=0",
    )
    assert v["wifi"] == "red|nicht verbunden · falsches Passwort"


def test_wifi_joining_and_retrying(cli):
    j, r = run(
        cli,
        BASE.replace("wstate=3", "wstate=2").replace("ip=3232281174", "ip=0") + " now=1 lang=0",
        BASE.replace("wstate=3", "wstate=4").replace("ip=3232281174", "ip=0") + " now=1 lang=0",
    )
    assert j["wifi"] == "amber|verbinde mit Insel"
    assert r["wifi"] == "amber|Insel nicht erreichbar, neuer Versuch"


def test_wifi_off_reasons_are_translated(cli):
    base = BASE.replace("wmode=2 wstate=3", "wmode=0 wstate=0").replace("ip=3232281174", "ip=0")
    got = [
        run(cli, base.replace("fail=", "fail=" + f) + " now=1 lang=%d" % lang)[0]["wifi"]
        for f, lang in [("not_found", 0), ("other", 0), ("auth", 1)]
    ]
    assert got == [
        "red|nicht verbunden · Netz nicht gefunden",
        "red|nicht verbunden · Verbindung fehlgeschlagen",
        "red|not connected · wrong password",
    ]


def test_wifi_long_ssid_truncates_exactly(cli):  # Review Focus 3
    long = "Ä" * 16  # 32 bytes UTF-8; the retrying row is then 64 bytes and gets cut
    (v,) = run(
        cli,
        BASE.replace("ssid=Insel", "ssid=" + long)
        .replace("wstate=3", "wstate=4")
        .replace("ip=3232281174", "ip=0")
        + " now=1 lang=0",
    )
    text = v["wifi"].split("|", 1)[1]
    assert text == long + " nicht erreichbar, neuer Versuc"
    assert len(text.encode()) == 63


def test_utf8_cut_backs_up_to_a_lead_byte(cli):
    # no real row puts a multi-byte char at the cut, so drive the cut itself: 62 ASCII + "Ä" (bytes 62-63)
    (out,) = run_raw(cli, "fit=" + "a" * 62 + "Ä")
    assert out == "fit=" + "a" * 62
    (out,) = run_raw(cli, "fit=" + "a" * 61 + "Ä")  # fits exactly (63 bytes): kept
    assert out == "fit=" + "a" * 61 + "Ä"


@pytest.mark.parametrize(
    "link,want", [(1, "green|verbunden · Daten vor 1 s"), (0, "red|Verbindung verloren, verbinde neu")]
)
def test_camper_bonded_after_reboot(cli, link, want):  # PAIR_IDLE with a stored address
    (v,) = run(
        cli, BASE.replace("pair=6", "pair=0").replace("link=1", "link=%d" % link) + " now=20000 lang=0"
    )
    assert v["camper"] == want


def test_stale_boundary(cli):
    a, b = run(
        cli,
        BASE.replace("age=800", "age=10000") + " now=1 lang=0",
        BASE.replace("age=800", "age=10001") + " now=1 lang=0",
    )
    assert a["camper"] == "green|verbunden · Daten vor 10 s" and b["camper"] == "red|keine Daten seit 10 s"


@pytest.mark.parametrize("ms,txt", [(61_000, "1 min"), (7_980_000, "2 h 13 min"), (273_600_000, "3 d 4 h")])
def test_device_uptime_formats(cli, ms, txt):  # Review Focus 4
    (v,) = run(cli, BASE.replace("up=7980000", "up=%d" % ms) + " now=%d lang=0" % ms)
    assert v["device"] == "green|läuft · seit " + txt


def test_english(cli):
    (v,) = run(cli, BASE + " now=7980000 lang=1")
    assert v["camper"] == "green|connected · data 1 s ago"


def test_brightness_numbers_do_not_rebrighten(cli):
    moved = (
        BASE.replace("age=800", "age=3100")
        .replace("rssi=-58", "rssi=-71")
        .replace("up=7980000", "up=8100000")
    )
    v = run(cli, "reset", BASE + " now=0 lang=0", BASE + " now=60000 lang=0", moved + " now=65000 lang=0")
    assert [x["bright"] for x in v] == ["100", "10", "10"]
    assert v[2]["camper"] == "green|verbunden · Daten vor 4 s"  # the text did change, the screen stays dim


def test_brightness_bright_then_dim_then_rebright(cli):
    v = run(
        cli,
        BASE + " now=0 lang=0",
        BASE + " now=59999 lang=0",
        BASE + " now=60000 lang=0",
        BASE.replace("link=1", "link=0") + " now=61000 lang=0",
    )
    assert [x["bright"] for x in v] == ["100", "100", "10", "100"]


def test_footer_template_is_exposed_for_the_view():
    src = (CORE / "display_model.c").read_text(encoding="utf-8")
    assert "cali_display_footer_setup_fmt" in src and "WEB_STR_DE_D_FOOTER_SETUP" in src

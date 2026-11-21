"""tools/fake_sensors_ble.py's frames against the decoder's own vectors and through ``sensors_cli``.

The builders must reproduce the vectors of test_sensors.py byte for byte (so a simulator fault cannot
look like a firmware fault), and drifted frames must decode in the C to the values the simulator logs.
"""

import pytest

from tools.fake_sensors_ble import (
    BMV_MODEL,
    MPPT_MODEL,
    adv_data,
    battery_plain,
    build,
    govee_mfg,
    mppt_plain,
    sensor_id,
    victron_mfg,
)

from . import test_sensors
from .test_sensors import BSENSE, GOVEE_H5074, GOVEE_H5075, KEY, SOLAR, victron
from .test_sensors_parity import _check, twin_battery, twin_govee, twin_mppt

cli = test_sensors.cli  # the shared sensors_cli build fixture


def test_frames_equal_the_decoder_vectors():
    assert govee_mfg("H5075", 24.3, 90.9, 100) == bytes.fromhex(GOVEE_H5075)
    assert govee_mfg("H5074", 23.96, 58.48, 90) == bytes.fromhex(GOVEE_H5074)
    assert mppt_plain(5, 0, 13.52, 2.3, 0.42, 87) == SOLAR
    assert battery_plain(12.98, 25.0) == BSENSE
    assert sensor_id("A4:C1:38:00:00:01") & 1 and 0 < sensor_id("A4:C1:38:00:00:01") <= 0xFFFF


def test_victron_container_equals_the_real_aes_vector():
    pytest.importorskip("cryptography")
    from .test_sensors_e2e import _victron_aes

    assert victron_mfg(0xA075, 0x01, SOLAR, KEY, 0xBEEF) == _victron_aes(0xA075, 0x01, SOLAR)


def test_every_fake_fits_a_legacy_advert_and_has_its_own_address():
    pytest.importorskip("cryptography")
    sensors = build(6, True, True, 21.0, 50.0, 90)
    for t in (0, 150, 300, 450):
        for s in sensors:
            assert len(s.frame(t)) <= 31
    assert len({s.addr for s in sensors}) == len({sensor_id(s.addr) for s in sensors}) == 8
    assert all(int(s.addr[:2], 16) >> 6 == 3 for s in sensors)  # static random: top bits 11
    assert adv_data(b"\x88\xec", "x") == b"\x02\x09x\x03\xff\x88\xec"


def _round(v):
    return {k: round(x, 2) for k, x in v.items()}


@pytest.mark.parametrize("model", ["H5075", "H5074"])
def test_drifted_govee_frames_decode_in_c_to_the_logged_values(cli, tmp_path, model):
    s = next(x for x in build(2, False, False, -1.0, 50.0, 77) if x.model == model)  # below 0: the sign path
    ts = range(0, 600, 37)
    got = _check(cli, tmp_path, [s.payload(t) for t in ts], twin_govee)
    assert [_round(g) for g in got] == [_round(s.values(t)) for t in ts]


def test_drifted_victron_plaintexts_decode_in_c_to_the_logged_values(cli, tmp_path):
    """The CLI's AES stub XORs the key, so the container goes through test_sensors.victron here."""
    mppt, bmv = build(0, True, True, 21.0, 50.0, 90)
    ts = range(0, 600, 61)
    advs = [bytes.fromhex(victron(MPPT_MODEL, 0x01, mppt.payload(t))) for t in ts]
    got = _check(cli, tmp_path, advs, twin_mppt, KEY.hex())
    assert [_round({k: g[k] for k in mppt.values(t)}) for g, t in zip(got, ts)] == [
        _round(mppt.values(t)) for t in ts
    ]
    bdir = tmp_path / "b"  # a fresh kv store: the configured MPPT would hide the battery's seen row
    bdir.mkdir()
    advs = [bytes.fromhex(victron(BMV_MODEL, 0x02, bmv.payload(t))) for t in ts]
    got = _check(cli, bdir, advs, twin_battery, KEY.hex())
    want = [{k: bmv.values(t)[k] for k in ("battery_voltage", "temperature_c")} for t in ts]
    assert [_round(g) for g in got] == [_round(w) for w in want]

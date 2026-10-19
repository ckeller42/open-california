"""sensors.c vs a pure-Python twin of buspi's readers: the same adverts, field-by-field equal values.

The C decoders were written from buspi's Python readers (``govee_reader.py``, ``victron_ble`` 0.9.3);
this twin restates those layouts and ~200 seeded random adverts per type (every sentinel at least once,
plus the live vectors of test_sensors.py) go through both the twin and ``sensors_cli``. Floats compare
rounded to 2 decimals (the ESP's JSON precision), NaN/n-a as ``None``.

The one intended asymmetry: company id ``0x0001`` is shared, so the C matches it only when the 24-bit
value is plausible (-40..80 C, battery <= 100). The twin (like ``_decode_24bit``) decodes any 6-byte
payload; implausible ones are asserted ignored by the C, decoded by the twin.

.. test:: Sensor decoders C/Python parity
   :id: T_FW_SENSORS_PARITY
   :links: R_FW_SENSORS
"""

import json
import random

from . import test_sensors
from .test_sensors import (
    BSENSE,
    GOVEE_GV5171,
    GOVEE_GV5179,
    GOVEE_H5074,
    GOVEE_H5075,
    KEY,
    SOLAR,
    run,
    victron,
)

cli = test_sensors.cli  # the shared sensors_cli build fixture

N = 200
rng = random.Random()  # seeded per generator


# ---- the twin: buspi's layouts, adv = company id LE + data ----------------------------------------


def _govee24(p):
    """govee_reader.py _decode_24bit: sign bit 23, temp*10000 + humidity*10, battery & 0x7F."""
    base = int.from_bytes(p[:3], "big")
    t = base & 0x7FFFFF
    sign = -1 if base & 0x800000 else 1
    return {"temp_c": (t // 1000) / 10 * sign, "humidity": (t % 1000) / 10, "battery": p[3] & 0x7F}


def twin_govee(adv):
    d = adv[2:]
    if adv[:2] == b"\x01\x00":  # 0x0001: [0..1] 01 01, [2..4] value, [5] battery (+ [6..7] on the GV5171)
        return _govee24(d[2:6]) if len(d) in (6, 8) else None
    if len(d) >= 7:  # H5074
        return {
            "temp_c": int.from_bytes(d[1:3], "little", signed=True) / 100,
            "humidity": int.from_bytes(d[3:5], "little") / 100,
            "battery": d[5],
        }
    return _govee24(d[1:5]) if len(d) >= 5 else None


def _plain(adv):
    """victron_ble container -> plaintext; the CLI's AES stub: keystream block = the key."""
    return bytes(c ^ k for c, k in zip(adv[10:26], KEY))


def _reader(plain):
    """victron_ble's LSB-first BitReader as a closure: read(n, signed=False)."""
    v, pos = int.from_bytes(plain, "little"), [0]

    def read(n, signed=False):
        x = v >> pos[0] & ((1 << n) - 1)
        pos[0] += n
        return x - (1 << n) if signed and x >> (n - 1) else x

    return read


def twin_mppt(adv):
    """SolarCharger.parse_decrypted; yield in kWh as the ESP reports it."""
    read = _reader(_plain(adv))
    state, err = read(8), read(8)
    v, i, y, pv, load = read(16, True), read(16, True), read(16), read(16), read(9)
    return {
        "battery_current": None if i == 0x7FFF else i / 10,
        "battery_voltage": None if v == 0x7FFF else v / 100,
        "charge_state": state,
        "error_code": err,
        "load_current": None if load == 0x1FF else load / 10,
        "pv_power": None if pv == 0xFFFF else pv,
        "yield_today_kwh": None if y == 0xFFFF else y * 10 / 1000,
    }


def twin_battery(adv):
    """BatteryMonitor.parse_decrypted (Smart Battery Sense shares it): voltage + aux temperature."""
    read = _reader(_plain(adv))
    read(16)  # remaining_mins
    v = read(16, True)
    read(16)  # alarm
    aux, mode = read(16), read(2)
    return {
        "battery_voltage": None if v == 0x7FFF else v / 100,
        "temperature_c": aux / 100 - 273.15 if mode == 2 else None,
    }


# ---- seeded adverts --------------------------------------------------------------------------------


def _rb(n):
    return bytes(rng.randrange(256) for _ in range(n))


def _pack(fields):  # [(value, nbits)] LSB-first -> int, nbits
    v = n = 0
    for val, nb in fields:
        v |= (val & ((1 << nb) - 1)) << n
        n += nb
    return v, n


def _maybe(sentinel, nbits, p=0.1):
    return sentinel if rng.random() < p else rng.randrange(1 << nbits)


def gen_govee_ec88():
    rng.seed("gen_govee_ec88")  # per generator: independent of test order
    out = [bytes.fromhex(GOVEE_H5075), bytes.fromhex(GOVEE_H5074)]
    for _ in range(N):
        out.append(b"\x88\xec" + _rb(rng.choice((5, 6, 7, 8, 9))))
    return out


def _gv0001(temp_tenths, hum_tenths, batt):
    t = abs(temp_tenths) * 1000 + hum_tenths
    base = t | (0x800000 if temp_tenths < 0 else 0)
    return b"\x01\x00\x01\x01" + base.to_bytes(3, "big") + bytes([batt])


def gen_govee_0001():
    rng.seed("gen_govee_0001")  # per generator: independent of test order
    plausible = [bytes.fromhex(GOVEE_GV5179), bytes.fromhex(GOVEE_GV5171)]
    for _ in range(N):
        batt = rng.randrange(101) | (0x80 if rng.random() < 0.2 else 0)  # bit 7: a flag, masked
        plausible.append(_gv0001(rng.randint(-400, 800), rng.randrange(1000), batt))
    implausible = [
        _gv0001(-401, 500, 50),
        _gv0001(801, 500, 50),
        _gv0001(200, 500, 101),
        _gv0001(200, 500, 0xFF),
    ]
    return plausible, implausible


def _vic(*a, **kw):
    return bytes.fromhex(victron(*a, **kw))


def gen_mppt():
    rng.seed("gen_mppt")  # per generator: independent of test order
    out = [_vic(0xA075, 0x01, SOLAR)]
    allna, n = _pack([(0, 8), (0, 8), (0x7FFF, 16), (0x7FFF, 16), (0xFFFF, 16), (0xFFFF, 16), (0x1FF, 9)])
    out.append(_vic(0xA075, 0x01, allna.to_bytes(12, "little")))
    for _ in range(N):
        v, n = _pack(
            [
                (rng.randrange(256), 8),
                (rng.randrange(256), 8),
                (_maybe(0x7FFF, 16), 16),
                (_maybe(0x7FFF, 16), 16),
                (_maybe(0xFFFF, 16), 16),
                (_maybe(0xFFFF, 16), 16),
                (_maybe(0x1FF, 9), 9),
                (rng.randrange(128), 7),
            ]
        )
        plain = v.to_bytes(12, "little") + _rb(rng.randrange(5))  # 12..16 bytes
        model = rng.choice((0xA040, 0xA075, 0xA05F, 0xA3A0))
        out.append(_vic(model, 0x01, plain, iv=rng.randrange(1 << 16)))
    return out


def gen_battery():
    rng.seed("gen_battery")  # per generator: independent of test order
    out = [_vic(0xA3A4, 0x02, BSENSE)]
    for _ in range(N):
        v, n = _pack(
            [
                (rng.randrange(1 << 16), 16),
                (_maybe(0x7FFF, 16), 16),
                (rng.randrange(1 << 16), 16),
                (rng.randrange(1 << 16), 16),
                (2 if rng.random() < 0.5 else rng.choice((0, 1, 3)), 2),
                (rng.randrange(1 << 62), 62),
            ]
        )
        plain = v.to_bytes(16, "little")[: rng.randint(9, 16)]
        model, mode = rng.choice(((0xA3A4, 0x02), (0xA3A5, 0x01), (0xA389, 0x02), (0xA38A, 0x02)))
        out.append(_vic(model, mode, plain, iv=rng.randrange(1 << 16)))
    return out


# ---- the C side ------------------------------------------------------------------------------------


def c_decode(cli, tmp_path, advs, key="-"):
    """Each advert through sensors_cli as one configured sensor: [(fields, decoded_now)] per advert.

    One process: the first advert makes the seen row, ``add`` configures it, then per advert
    ``now T`` / ``adv`` / ``state T``. ``age_s == 0`` = this advert decoded; else the C ignored it."""
    addr = "A4:C1:38:00:00:09"
    first = "adv %s 1 -60 S_0009 %s" % (addr, advs[0].hex())
    sid = json.loads(run(cli, tmp_path, ["init", first, "api 0"])[-1][4:])["seen"][0]["id"]
    lines = ["init", first, "add %d s %s" % (sid, key)]
    for i, a in enumerate(advs):
        t = (i + 1) * 1000
        lines += ["now %d" % t, "adv %s 1 -60 S_0009 %s" % (addr, a.hex()), "state %d" % t]
    out = [json.loads(x[4:])["s"] for x in run(cli, tmp_path, lines) if x.startswith("EXT ")]
    assert len(out) == len(advs)
    assert all(
        r["type"] in ("govee", "victron_mppt", "victron_battery") for r in out
    )  # every entry names its type
    return [({k: v for k, v in r.items() if k not in ("age_s", "type")}, r["age_s"] == 0) for r in out]


def _r2(fields):
    return {k: None if v is None else round(v, 2) for k, v in fields.items()}


def _check(cli, tmp_path, advs, twin, key="-"):
    got = c_decode(cli, tmp_path, advs, key)
    for adv, (c, fresh) in zip(advs, got):
        assert fresh, "C ignored %s" % adv.hex()
        assert _r2(c) == _r2(twin(adv)), adv.hex()
    return [twin(a) for a in advs]


def test_govee_ec88_parity(cli, tmp_path):
    _check(cli, tmp_path, gen_govee_ec88(), twin_govee)


def test_govee_0001_parity_and_plausibility_gate_is_c_only(cli, tmp_path):
    plausible, implausible = gen_govee_0001()
    _check(cli, tmp_path, plausible, twin_govee)
    advs = [plausible[0]] + implausible  # a decoded reading, then the ones the C must drop
    gate = tmp_path / "gate"  # a fresh kv store: the first run's configured sensor would hide the seen row
    gate.mkdir()
    got = c_decode(cli, gate, advs)
    assert got[0][1] and not any(fresh for _, fresh in got[1:])
    assert all(twin_govee(a) is not None for a in implausible)  # the twin decodes them: the gate is C-only


def test_victron_mppt_parity_with_every_sentinel(cli, tmp_path):
    vals = _check(cli, tmp_path, gen_mppt(), twin_mppt, KEY.hex())
    for f in ("battery_current", "battery_voltage", "load_current", "pv_power", "yield_today_kwh"):
        assert any(v[f] is None for v in vals) and any(v[f] is not None for v in vals), f


def test_victron_battery_parity_with_every_sentinel(cli, tmp_path):
    vals = _check(cli, tmp_path, gen_battery(), twin_battery, KEY.hex())
    for f in ("battery_voltage", "temperature_c"):
        assert any(v[f] is None for v in vals) and any(v[f] is not None for v in vals), f

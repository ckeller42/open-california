"""Fake Govee thermometers and Victron devices on a Linux Bluetooth adapter, for the satellite's
sensor window (R_FW_SENSORS, ``firmware/components/cali_core/sensors.c``).

The satellite tells sensors apart by their BLE address (the seen id is a CRC of it), so every fake
sensor gets its own legacy, non-connectable advertising set with a fixed static random address. BlueZ
cannot do that: a D-Bus or ``btmgmt`` advertisement has no address option, and by the kernel source
(``hci_get_random_address``) a non-connectable instance gets a fresh non-resolvable private address
each time the kernel sets it up (every data change), which would orphan a configured sensor. So this tool sends the LE extended
advertising HCI commands itself, over a raw HCI socket next to bluetoothd, on set handles at the top
of the range (the kernel numbers its own instances from 1 upwards). It needs root (CAP_NET_RAW) and a
controller with LE extended advertising (Bluetooth 5).

The satellite scans passively, so the name rides in the advertising data, not a scan response. The
values drift slowly (a sine of a 10-minute period); every ``--interval`` seconds the sets are set up
again with fresh frames, which also restores them if the kernel cleared all sets (adapter reset).

Deps: stdlib; ``cryptography`` for the Victron AES-CTR container (imported only when a Victron runs).

    sudo python3 tools/fake_sensors_ble.py --govee 3 --mppt --battery-monitor
    sudo python3 tools/fake_sensors_ble.py --govee 3 --mppt --battery-monitor --list
    sudo python3 tools/fake_sensors_ble.py --stop     # remove sets a killed run left behind
"""

from __future__ import annotations

import argparse
import hashlib
import math
import select
import signal
import socket
import struct
import sys
import time
import zlib
from dataclasses import dataclass

GOVEE_MFR = 0xEC88
VICTRON_MFR = 0x02E1
MPPT_MODEL = 0xA075  # SmartSolar MPPT 75/15, the vectors' model
BMV_MODEL = 0xA389  # BMV-712 Smart (battery monitor, readout type 0x02)
MAX_SETS = 8  # handles 0xEF downwards
TOP_HANDLE = 0xEF


# ---- frames (pure: no Bluetooth) -------------------------------------------------------------------


def bits(fields) -> bytes:
    """``[(value, nbits)]`` packed LSB-first (Victron's BitReader order) into whole bytes.

    :param fields: (value, width) pairs; a negative value is stored as two's complement of its width.
    :returns: the packed bytes, little-endian.
    """
    v = n = 0
    for val, nb in fields:
        v |= (val & ((1 << nb) - 1)) << n
        n += nb
    return v.to_bytes((n + 7) // 8, "little")


def govee_mfg(model: str, temp_c: float, humidity: float, battery: int) -> bytes:
    """The Govee manufacturer data (company id LE + payload) as ``decode_govee`` reads it.

    :param model: ``"H5075"`` (6-byte payload, 24-bit value) or ``"H5074"`` (7 bytes, int16 pairs).
    :returns: the MANUFACTURER_SPECIFIC_DATA value.
    """
    head = GOVEE_MFR.to_bytes(2, "little") + b"\x00"
    if model == "H5074":
        t, h = round(temp_c * 100), round(humidity * 100)
        return head + struct.pack("<hHB", t, h, battery) + b"\x02"
    t10, h10 = round(temp_c * 10), round(humidity * 10)
    base = abs(t10) * 1000 + h10 | (0x800000 if t10 < 0 else 0)
    return head + base.to_bytes(3, "big") + bytes([battery, 0])


def _na(v, scale, sentinel):
    return sentinel if v is None else round(v * scale)


def mppt_plain(state, error, voltage, current, yield_kwh, pv_w, load_a=None) -> bytes:
    """The SmartSolar plaintext (12 bytes); ``None`` = the field's n/a sentinel.

    :returns: charge_state u8, error u8, voltage s16 0.01 V, current s16 0.1 A, yield u16 0.01 kWh,
        pv u16 W, load u9 0.1 A, padded to 12 bytes.
    """
    return bits([(state, 8), (error, 8), (_na(voltage, 100, 0x7FFF), 16), (_na(current, 10, 0x7FFF), 16),
                 (_na(yield_kwh, 100, 0xFFFF), 16), (_na(pv_w, 1, 0xFFFF), 16), (_na(load_a, 10, 0x1FF), 9)])  # fmt: skip


def battery_plain(voltage, temp_c, remaining_min=None, current_a=None, consumed_ah=None, soc=None) -> bytes:
    """The battery monitor plaintext (18 bytes, aux = temperature); ``None`` = the n/a sentinel.

    :returns: remaining u16 min, voltage s16 0.01 V, alarm u16, aux u16 0.01 K, aux_mode u2 (2),
        current s22 mA, consumed u20 0.1 Ah (drawn, positive), soc u10 0.1 %.
    """
    aux = 0xFFFF if temp_c is None else round((temp_c + 273.15) * 100)
    return bits([(_na(remaining_min, 1, 0xFFFF), 16), (_na(voltage, 100, 0x7FFF), 16), (0, 16), (aux, 16), (2, 2),
                 (_na(current_a, 1000, 0x3FFFFF), 22), (_na(consumed_ah, 10, 0xFFFFF), 20), (_na(soc, 10, 0x3FF), 10)])  # fmt: skip


def victron_mfg(model: int, mode: int, plain: bytes, key: bytes, iv: int) -> bytes:
    """The Instant Readout container: AES-128-CTR, counter block = iv as a 128-bit LE integer.

    :param mode: the readout type (0x01 solar charger, 0x02 battery monitor).
    :param key: the 16-byte advertisement key (VictronConnect shows it as 32 hex digits).
    :returns: the MANUFACTURER_SPECIFIC_DATA value.
    """
    from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

    enc = Cipher(algorithms.AES(key), modes.CTR(iv.to_bytes(2, "little") + bytes(14))).encryptor()
    head = VICTRON_MFR.to_bytes(2, "little") + b"\x10\x00" + model.to_bytes(2, "little")
    return head + bytes([mode]) + iv.to_bytes(2, "little") + key[:1] + enc.update(plain) + enc.finalize()


def adv_data(mfg: bytes, name: str = "") -> bytes:
    """Legacy advertising data: a complete local name (if any) + the manufacturer data, no flags.

    :raises ValueError: over the 31-byte legacy limit.
    """
    out = (bytes([len(name) + 1, 0x09]) + name.encode() if name else b"") + bytes([len(mfg) + 1, 0xFF]) + mfg
    if len(out) > 31:
        raise ValueError("advert %d > 31 bytes" % len(out))
    return out


def sensor_id(addr: str) -> int:
    """The satellite's boot-local id of an address (``sens_id``: zlib CRC-32 of the 6 bytes as written, low 16 bits | 1)."""
    return zlib.crc32(bytes.fromhex(addr.replace(":", ""))) & 0xFFFF | 1


# ---- the fake sensors -------------------------------------------------------------------------------


@dataclass
class Sensor:
    """One fake sensor: its kind (``govee``/``mppt``/``battery``), address, name and base values."""

    kind: str
    addr: str
    name: str
    model: str = ""
    temp: float = 21.0
    humidity: float = 50.0
    battery: int = 90
    key: bytes = b""
    iv: int = 0
    t0: float = 0.0

    def values(self, t: float) -> dict:
        """The drifted values at time ``t`` (s): a slow sine, its phase per address."""
        s = math.sin(2 * math.pi * (t - self.t0) / 600 + sensor_id(self.addr))
        if self.kind == "govee":
            nd = 2 if self.model == "H5074" else 1  # the layout's resolution: what the satellite shows
            hum = min(
                max(self.humidity + 3 * s, 0.0), 99.9
            )  # H5075: 100 % or more spills into the temperature
            return {"temp_c": round(self.temp + 0.5 * s, nd), "humidity": round(hum, nd),
                    "battery": min(max(self.battery, 0), 100)}  # fmt: skip
        if self.kind == "mppt":
            v, pv = round(13.5 + 0.2 * s, 2), round(90 + 40 * s)
            return {"charge_state": 3, "error_code": 0, "battery_voltage": v, "battery_current": round(pv / v, 1),
                    "pv_power": pv, "yield_today_kwh": round(0.42 + (t - self.t0) * 90 / 3.6e6, 2)}  # fmt: skip
        return {"battery_voltage": round(12.9 + 0.1 * s, 2), "temperature_c": round(18 + 2 * s, 2),
                "current_a": round(-3 + s, 3), "soc": round(85 + 2 * s, 1)}  # fmt: skip

    def payload(self, t: float) -> bytes:
        """The Govee manufacturer data, or the Victron plaintext (before AES), at time ``t``."""
        v = self.values(t)
        if self.kind == "govee":
            return govee_mfg(self.model, v["temp_c"], v["humidity"], v["battery"])
        if self.kind == "mppt":
            return mppt_plain(v["charge_state"], v["error_code"], v["battery_voltage"], v["battery_current"],
                              v["yield_today_kwh"], v["pv_power"])  # fmt: skip
        return battery_plain(v["battery_voltage"], v["temperature_c"], 600, v["current_a"], 12.0, v["soc"])

    def frame(self, t: float) -> bytes:
        """The advertising data at time ``t``; a Victron counts its iv up per frame, as a real one per new reading."""
        if self.kind == "govee":
            return adv_data(self.payload(t), self.name)
        self.iv = (self.iv + 1) & 0xFFFF
        model, mode = (MPPT_MODEL, 0x01) if self.kind == "mppt" else (BMV_MODEL, 0x02)
        return adv_data(victron_mfg(model, mode, self.payload(t), self.key, self.iv), self.name)


def _key(addr: str) -> bytes:
    """A fixed test key per address (no secret: printed by --list)."""
    return hashlib.sha256(b"fake_sensors_ble " + addr.encode()).digest()[:16]


def build(govee: int, mppt: bool, battery_monitor: bool, temp: float, humidity: float, battery: int) -> list:
    """The fake sensors: Govees alternate H5075/H5074, each 1 C warmer than the one before.

    Addresses are static random (top bits 11), fixed per slot: CA:FE:00:00:01:0n Govee n, :02:01 the
    MPPT, :03:01 the battery monitor — a configured sensor finds its fake again on the next run.
    """
    out = []
    for i in range(govee):
        addr, model = "CA:FE:00:00:01:%02X" % (i + 1), ("H5075", "H5074")[i % 2]
        out.append(Sensor("govee", addr, "GV%s_%s" % (model, addr[-5:].replace(":", "")), model,
                          temp + i, humidity, battery))  # fmt: skip
    if mppt:  # name <= 5 chars: name AD 7 + mfg AD 24 = the 31-byte legacy advert
        out.append(Sensor("mppt", "CA:FE:00:00:02:01", "SS_01", key=_key("CA:FE:00:00:02:01")))
    if (
        battery_monitor
    ):  # 18-byte plaintext: mfg AD 30, no room for a name (a real BMV names itself in the scan response)
        out.append(Sensor("battery", "CA:FE:00:00:03:01", "", key=_key("CA:FE:00:00:03:01")))
    if len(out) > MAX_SETS:
        raise SystemExit("at most %d fake sensors" % MAX_SETS)
    return out


# ---- HCI -------------------------------------------------------------------------------------------


class Hci:
    """A raw HCI socket: LE controller commands, each awaiting its Command Complete/Status."""

    def __init__(self, dev: int):
        self.s = socket.socket(socket.AF_BLUETOOTH, socket.SOCK_RAW, socket.BTPROTO_HCI)
        self.s.setsockopt(
            socket.SOL_HCI,
            socket.HCI_FILTER,
            struct.pack("<IIIH2x", 1 << 4, ~0 & 0xFFFFFFFF, ~0 & 0xFFFFFFFF, 0),
        )
        self.s.bind((dev,))

    def le(self, ocf: int, params: bytes = b"") -> bytes:
        """Send LE command ``ocf``; its return parameters (status first).

        :raises TimeoutError: no reply in 2 s.
        """
        op = 0x08 << 10 | ocf
        self.s.send(struct.pack("<BHB", 1, op, len(params)) + params)
        end = time.monotonic() + 2
        while (left := end - time.monotonic()) > 0:
            if not select.select([self.s], [], [], left)[0]:
                break
            ev = self.s.recv(300)
            if ev[1] == 0x0E and struct.unpack_from("<H", ev, 4)[0] == op:
                return ev[6:]
            if ev[1] == 0x0F and struct.unpack_from("<H", ev, 5)[0] == op:
                return ev[3:4]
        raise TimeoutError("HCI LE 0x%04x: no reply" % ocf)

    def check(self, what: str, ocf: int, params: bytes = b"") -> None:
        """:meth:`le`, raising on a non-zero status."""
        st = self.le(ocf, params)[0]
        if st:
            raise OSError("%s: HCI status 0x%02x" % (what, st))

    def start(self, handle: int, addr: str, data: bytes, interval_ms: int = 100) -> None:
        """(Re)start set ``handle``: legacy ADV_NONCONN_IND from static random ``addr``, every ``interval_ms``."""
        self.le(0x39, bytes([0, 1, handle, 0, 0, 0]))  # disable first: parameters need a disabled set
        iv = round(interval_ms / 0.625).to_bytes(3, "little")
        self.check("set parameters", 0x36, bytes([handle]) + struct.pack("<H", 0x0010) + iv + iv
                   + bytes([0x07, 0x01, 0x00]) + bytes(6) + bytes([0x00, 0x7F, 0x01, 0x00, 0x01, handle & 0x0F, 0x00]))  # fmt: skip
        self.check("set address", 0x35, bytes([handle]) + bytes.fromhex(addr.replace(":", ""))[::-1])
        self.check("set data", 0x37, bytes([handle, 0x03, 0x01, len(data)]) + data)
        self.check("enable", 0x39, bytes([1, 1, handle, 0, 0, 0]))

    def stop(self, handle: int) -> None:
        """Disable and remove set ``handle`` (errors ignored: it may not exist)."""
        self.le(0x39, bytes([0, 1, handle, 0, 0, 0]))
        self.le(0x3C, bytes([handle]))


def describe(sensors, t: float) -> None:
    """Print what is advertised: address, satellite id, name, values and the Victron keys."""
    for s in sensors:
        print("%-7s %s id=%-5d name=%-13r %s" % (s.kind, s.addr, sensor_id(s.addr), s.name, s.values(t)))
        if s.key:
            print("        VictronConnect would show: %s" % s.key.hex())


def _ignore_signals() -> None:
    for sig in (signal.SIGTERM, signal.SIGINT):
        signal.signal(sig, signal.SIG_IGN)


def _quit(*_) -> None:
    """SIGTERM/SIGINT: ignore further ones first (``sudo`` relays a pkill's signal, so two arrive), then unwind to the cleanup."""
    _ignore_signals()
    raise SystemExit(0)


def main(argv=None) -> int:
    """CLI: advertise until Ctrl-C/SIGTERM, then remove every set."""
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--govee", type=int, default=3, help="fake Govee thermometers (default 3)")
    ap.add_argument("--mppt", action="store_true", help="add one Victron SmartSolar MPPT")
    ap.add_argument("--battery-monitor", action="store_true", help="add one Victron BMV-712 battery monitor")
    ap.add_argument(
        "--temp", type=float, default=21.0, help="the first Govee's temperature, C (each next +1)"
    )
    ap.add_argument("--humidity", type=float, default=50.0, help="Govee humidity, %%")
    ap.add_argument("--battery", type=int, default=90, help="Govee battery, %%")
    ap.add_argument("--interval", type=float, default=10.0, help="seconds between value updates")
    ap.add_argument("--hci", type=int, default=0, help="adapter index (default 0 = hci0)")
    ap.add_argument("--list", action="store_true", help="print the sensors and keys, advertise nothing")
    ap.add_argument("--stop", action="store_true", help="remove this tool's sets, advertise nothing")
    a = ap.parse_args(argv)
    sensors = build(a.govee, a.mppt, a.battery_monitor, a.temp, a.humidity, a.battery)
    t0 = time.time()
    for s in sensors:
        s.t0 = t0
    if a.list:
        describe(sensors, t0)
        return 0
    hci = Hci(a.hci)
    handles = [TOP_HANDLE - i for i in range(len(sensors))]
    if a.stop:
        for h in range(TOP_HANDLE, TOP_HANDLE - MAX_SETS, -1):
            hci.stop(h)
        return 0
    signal.signal(signal.SIGTERM, _quit)
    signal.signal(signal.SIGINT, _quit)
    describe(sensors, t0)
    try:
        while True:
            t = time.time()
            for h, s in zip(handles, sensors):
                hci.start(h, s.addr, s.frame(t))
            print(
                time.strftime("%H:%M:%S"),
                " | ".join("%s %s" % (s.addr[-5:], s.values(t)) for s in sensors),
                flush=True,
            )
            time.sleep(a.interval)
    except SystemExit:
        pass
    finally:
        _ignore_signals()
        for h in handles:
            hci.stop(h)
        print("advertising stopped", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())

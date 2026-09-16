"""BLE trace recorder — every notification, read, write and link event of the REAL unit as JSONL.

Off unless ``CALICTL_BLE_TRACE=<path.jsonl>`` is set; then :func:`get` returns a live
:class:`Tracer` that :mod:`calictl.device` calls at each GATT I/O site. One line per event::

    {"t": 1789567890.123, "ev": "notify", "char": "1602", "fn": "energy", "hex": "00d0800a…"}
    {"t": …, "ev": "write",  "char": "1701", "fn": "airheater", "hex": "3d05003c0c00"}
    {"t": …, "ev": "connect", "addr": "XX:XX:…"}

Why: the mock (``tools/mock_unit.py``) and the app lab (``tools/applab``) are only as good as the
real unit's behaviour they imitate. A trace taken on buspi while the van is awake is the ground
truth ``tools/trace_compare.py`` replays through the mock — frame round-trips, notification
cadence, countdown rates, ignition coupling — and reports where the mock differs.

Stdlib-only, append-only, size-capped (one rotated predecessor ``<path>.1``). The 1003
heartbeat (~1.5 writes/s for hours) is skipped unless ``CALICTL_BLE_TRACE_HEARTBEAT=1``.

.. req:: Record every BLE event of the real unit for replay
   :id: R_BLE_TRACE
   :status: implemented
   :tags: ble, tooling, evidence

   With ``CALICTL_BLE_TRACE`` set, ``calictl`` shall append one JSON line per GATT
   notification, read, write and link event (epoch time, short char id, function name,
   payload hex), skip the ``1003`` heartbeat unless asked, cap the file size, and stay a
   no-op otherwise.
"""
from __future__ import annotations

import json
import os
import time
from collections.abc import Iterator

DEFAULT_MAX_BYTES = 50 * 1024 * 1024
_FN_BY_CHAR: dict[str, str] | None = None
_TRACER: Tracer | None = None


def char_short(uuid: str) -> str:
    """``00001602-6c77-…`` -> ``1602`` (the 4-hex slot); other UUIDs pass through lowercased."""
    u = str(uuid).lower()
    return u[4:8] if len(u) >= 8 and u[8:9] == "-" else u


def fn_name(uuid: str) -> str | None:
    """Function name owning a state/control char (``1602`` -> ``energy``), ``heartbeat`` for 1003."""
    global _FN_BY_CHAR
    if _FN_BY_CHAR is None:
        from . import overrides, protocol  # lazy: keep import cheap for the no-trace path
        funcs = protocol.load()
        overrides.apply(funcs)
        m: dict[str, str] = {"1003": "heartbeat"}
        for name, f in funcs.items():
            for c in (f.state_char, f.control_char):
                if c:
                    m[char_short(c)] = name
        _FN_BY_CHAR = m
    return _FN_BY_CHAR.get(char_short(uuid))


class Tracer:
    """Appends trace events to a JSONL file; a ``Tracer(None)`` is a silent no-op."""

    def __init__(self, path: str | None, heartbeat: bool = False, max_bytes: int = DEFAULT_MAX_BYTES):
        self.path = path
        self.heartbeat = heartbeat
        self.max_bytes = max_bytes

    @classmethod
    def from_env(cls, max_bytes: int | None = None) -> Tracer:
        return cls(os.environ.get("CALICTL_BLE_TRACE") or None,
                   heartbeat=os.environ.get("CALICTL_BLE_TRACE_HEARTBEAT", "") not in ("", "0"),
                   max_bytes=max_bytes or DEFAULT_MAX_BYTES)

    @property
    def enabled(self) -> bool:
        return bool(self.path)

    # --- events ----------------------------------------------------------------------------
    def notify(self, uuid: str, data: bytes) -> None:
        self._io("notify", uuid, data)

    def read(self, uuid: str, data: bytes) -> None:
        self._io("read", uuid, data)

    def write(self, uuid: str, data: bytes) -> None:
        self._io("write", uuid, data)

    def link(self, ev: str, address: str | None, **extra) -> None:
        if not self.path:
            return
        rec = {"t": time.time(), "ev": ev, "addr": address}
        rec.update(extra)
        self._emit(rec)

    # --- plumbing --------------------------------------------------------------------------
    def _io(self, ev: str, uuid: str, data: bytes) -> None:
        if not self.path:
            return
        short = char_short(uuid)
        if short == "1003" and not self.heartbeat:
            return
        self._emit({"t": time.time(), "ev": ev, "char": short, "fn": fn_name(uuid),
                    "hex": bytes(data).hex()})

    def _emit(self, rec: dict) -> None:
        try:
            if os.path.exists(self.path) and os.path.getsize(self.path) >= self.max_bytes:
                os.replace(self.path, self.path + ".1")        # keep one predecessor
            with open(self.path, "a", encoding="utf-8") as f:
                f.write(json.dumps(rec, separators=(",", ":")) + "\n")
        except OSError:
            pass                                               # tracing must never break BLE I/O


def get() -> Tracer:
    """The process-wide tracer, configured from the environment on first use."""
    global _TRACER
    if _TRACER is None:
        _TRACER = Tracer.from_env()
    return _TRACER


def read_events(path: str | os.PathLike) -> Iterator[dict]:
    """Yield the trace's events in file order (a rotated ``<path>.1`` predecessor first if present)."""
    for p in (str(path) + ".1", str(path)):
        if not os.path.exists(p):
            continue
        with open(p, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    yield json.loads(line)

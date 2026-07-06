"""calictl command-line interface.

    calictl status                 # read every function, show interpreted status
    calictl get <function>         # one function: decoded fields + interpretation
    calictl raw <function>         # raw hex of the state characteristic
    calictl set cooler power on|off
    calictl set cooler level <1-5>

Read commands are fully live-verified. `set` builds the correct full-packet
frame (verified against the app's frame builder) but a live control write has
not yet been confirmed on-device, so it reads the state back and reports whether
the change actually took effect.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys

from . import protocol, semantics, overrides
from .device import CamperDevice, ConnectionUnavailable


def _load():
    funcs = protocol.load()
    overrides.apply(funcs)
    return funcs


def _fmt(v):
    if isinstance(v, bool):
        return "yes" if v else "no"
    return v


def _render_status(name, interp):
    if not interp.get("installed", True) and "installed" in interp:
        return "  %-16s not installed" % name
    parts = []
    for k, v in interp.items():
        if k in ("installed", "raw"):
            continue
        if isinstance(v, dict):
            v = " ".join("%s=%s" % (kk, _fmt(vv)) for kk, vv in v.items())
        parts.append("%s=%s" % (k, _fmt(v)))
    return "  %-16s %s" % (name, "  ".join(str(p) for p in parts))


async def cmd_status(funcs, dev, args):
    raw = await dev.read_all(funcs)
    print("VWCAMPER status (%d functions read)\n" % len(raw))
    for name in sorted(funcs):
        if name not in raw:
            continue
        decoded = protocol.decode(funcs[name], raw[name])
        interp = semantics.interpret(name, decoded)
        print(_render_status(name, interp))


async def cmd_get(funcs, dev, args):
    f = funcs[args.function]
    decoded = protocol.decode(f, await dev.read(f))
    print(json.dumps({"function": args.function,
                      "decoded": decoded,
                      "interpreted": semantics.interpret(args.function, decoded)},
                     indent=2, default=str))


async def cmd_raw(funcs, dev, args):
    f = funcs[args.function]
    print((await dev.read(f)).hex())


def _cooler_values(state: dict, **changes) -> dict:
    """Full-packet cooler control values: carry current State/Mode/Level and
    timer values, timer ACTION fields at no-op, then apply `changes`."""
    vals = dict(
        State=state.get("State", 1), Mode=state.get("Mode", 4),
        Level=state.get("Level", 3),
        TimerStart=3, TimerCancel=3, NightTimerSet=0,   # no-op actions
        NightTimerHourOff=0, NightTimerHourOn=0,
        TimerHour=state.get("TimerHourSet", 0), TimerMin=state.get("TimerMinSet", 0),
    )
    vals.update(changes)
    return vals


async def cmd_set(funcs, dev, args):
    if args.function != "cooler":
        print("set is currently implemented only for 'cooler'", file=sys.stderr)
        return 2
    f = funcs["cooler"]
    cur = protocol.decode(f, await dev.read(f))
    print("current: State=%s Mode=%s Level=%s" % (cur.get("State"), cur.get("Mode"), cur.get("Level")))
    if args.what == "power":
        target = {"State": 1 if args.value == "on" else 0}
        check = ("State", target["State"])
    elif args.what == "level":
        lvl = int(args.value)
        if not 1 <= lvl <= 5:
            print("level must be 1-5", file=sys.stderr); return 2
        target = {"Level": lvl}
        check = ("Level", lvl)
    else:
        print("unknown target %r" % args.what, file=sys.stderr); return 2
    frame = protocol.encode(f, _cooler_values(cur, **target), frame_bytes=6)
    print("writing %s to cooler control ..." % frame.hex())
    post = await dev.write_control(f, frame, verify=True)
    field, want = check
    got = post.get(field) if post else None
    ok = got == want
    print("after write: %s=%s (wanted %s) -> %s" % (field, got, want, "OK" if ok else "NOT APPLIED"))
    return 0 if ok else 1


def build_parser():
    p = argparse.ArgumentParser(prog="calictl", description="Control the VW California Camper Unit over BLE")
    p.add_argument("--addr", default=CamperDevice().addr, help="BLE identity address")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("status")
    g = sub.add_parser("get"); g.add_argument("function")
    r = sub.add_parser("raw"); r.add_argument("function")
    s = sub.add_parser("set")
    s.add_argument("function"); s.add_argument("what"); s.add_argument("value")
    d = sub.add_parser("daemon", help="bridge to Home Assistant over MQTT")
    d.add_argument("--broker"); d.add_argument("--port", type=int, default=1883)
    d.add_argument("--interval", type=float, default=30.0)
    d.add_argument("--dry-run", action="store_true")
    return p


def main(argv=None):
    args = build_parser().parse_args(argv)
    if args.cmd == "daemon":
        from . import daemon
        if args.dry_run or not args.broker:
            asyncio.run(daemon.dry_run())
        else:
            daemon.run(args.broker, args.port, args.interval, args.addr)
        return 0
    funcs = _load()
    if getattr(args, "function", None) and args.function not in funcs:
        print("unknown function %r. known: %s" % (args.function, ", ".join(sorted(funcs))),
              file=sys.stderr)
        return 2
    dev = CamperDevice(args.addr)
    handler = {"status": cmd_status, "get": cmd_get, "raw": cmd_raw, "set": cmd_set}[args.cmd]
    try:
        return asyncio.run(handler(funcs, dev, args)) or 0
    except ConnectionUnavailable as e:
        print("BLE unavailable: %s" % e, file=sys.stderr)
        return 3


if __name__ == "__main__":
    raise SystemExit(main())

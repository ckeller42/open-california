"""calictl command-line interface.

    calictl status                 # read every function, show interpreted status
    calictl get <function>         # one function: decoded fields + interpretation
    calictl raw <function>         # raw hex of the state characteristic
    calictl set cooler power on|off
    calictl set cooler level <1-5>
    calictl set campingmode master|lights|usb on|off
    calictl set lighting power on|off
    calictl set lighting brightness <0-15>
    calictl set airheater power on|off
    calictl set airheater level <0-15>

Read commands are fully live-verified. `set` builds the full-packet frame and
writes it under a 1003 liveness heartbeat (`device.actuate`), which arms
actuation on-device (issue #2); it reads the state back and reports whether the
change actually took effect.
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


def _set_check(function, what, value, interp, decoded):
    """Post-write success check: (label, got, want). `got == want` -> OK. Reads the
    targeted field from the interpreted state (or the raw decode for cooler numerics)."""
    on = str(value).strip().lower() in ("on", "true", "1")
    table = {
        ("cooler", "power"):        lambda: ("State", decoded.get("State"), 1 if on else 0),
        ("cooler", "level"):        lambda: ("Level", decoded.get("Level"), int(value)),
        ("campingmode", "master"):  lambda: ("master_on", interp.get("master_on"), on),
        ("campingmode", "usb"):     lambda: ("usb_charger", interp.get("usb_charger"), on),
        ("campingmode", "lights"):  lambda: ("lights_on", interp.get("lights_on"), on),
        ("lighting", "power"):      lambda: ("any_on", interp.get("any_on"), on),
        ("lighting", "brightness"): lambda: ("max_zone", _max_zone(interp), int(value)),
        ("airheater", "power"):     lambda: ("running", interp.get("running"), on),
        ("airheater", "level"):     lambda: ("level", interp.get("level"), int(value)),
    }
    fn = table.get((function, what))
    return fn() if fn else (what, None, None)


def _max_zone(interp):
    return max((interp.get("brightness_zone_%d" % z) or 0 for z in range(1, 17)), default=0)


async def cmd_set(funcs, dev, args):
    from . import control
    fn = args.function
    if fn not in control.BUILDERS:
        print("set not implemented for %r (have: %s)"
              % (fn, ", ".join(sorted(control.BUILDERS))), file=sys.stderr)
        return 2
    f = funcs[fn]
    cur = protocol.decode(f, await dev.read(f))
    print("current %s: %s" % (fn, json.dumps(semantics.interpret(fn, cur), default=str)))
    try:
        frame = control.build(funcs, fn, args.what, args.value, cur)
    except ValueError as e:
        print(str(e), file=sys.stderr); return 2
    if frame is None:
        print("unknown target %r for %s" % (args.what, fn), file=sys.stderr); return 2
    print("writing %s to %s control (heartbeat-armed) ..." % (frame.hex(), fn))
    post = await dev.actuate(f, frame, verify=True)
    if post is None:
        print("write sent, no readback"); return 1
    interp = semantics.interpret(fn, post)
    label, got, want = _set_check(fn, args.what, args.value, interp, post)
    ok = got == want
    print("after write: %s=%s (wanted %s) -> %s" % (label, got, want, "OK" if ok else "NOT APPLIED"))
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
    i = sub.add_parser("influx", help="single InfluxDB test write; the serve daemon does this continuously")
    sv = sub.add_parser("serve", help="unified daemon: one BLE owner -> InfluxDB + MQTT + commands")
    sv.add_argument("--interval", type=float, default=30.0)
    sv.add_argument("--no-influx", action="store_true", help="skip InfluxDB writes")
    sv.add_argument("--dry-run", action="store_true", help="print discovery + one poll, no broker/InfluxDB")
    return p


def main(argv=None):
    args = build_parser().parse_args(argv)
    if args.cmd == "influx":
        from . import influx
        influx.write_once(args.addr)
        return 0
    if args.cmd == "serve":
        from . import serve
        if args.dry_run:
            asyncio.run(serve.dry_run(args.addr))
        else:
            serve.Server(args.addr, interval=args.interval,
                         influx_enabled=not args.no_influx).run()
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

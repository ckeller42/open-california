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
    calictl set roofaircondition power on|off       # UNVERIFIED (not installed)
    calictl set roofaircondition fanspeed <0-4>     # UNVERIFIED
    calictl set roofaircondition mode <0-3>         # UNVERIFIED
    calictl set roofaircondition temperature <0-255># UNVERIFIED (raw)
    calictl set stairs move extend|retract|stop     # UNVERIFIED (not installed)
    calictl set stairs mode <0-3>                    # UNVERIFIED
    calictl set livingroomheater air on|off          # UNVERIFIED (not installed)
    calictl set livingroomheater water on|off        # UNVERIFIED
    calictl set livingroomheater temperature <0-255> # UNVERIFIED (raw)
    calictl set roof open|close|stop                 # SAFETY-SENSITIVE + UNVERIFIED

Read commands are fully live-verified. `set` builds the full-packet frame and
writes it under a 1003 liveness heartbeat (`device.actuate`), which arms
actuation on-device (issue #2); it reads the state back and reports whether the
change actually took effect.

The roofaircondition/stairs/livingroomheater/roof targets are NOT-LIVE-VERIFIED
(none is installed on this van) — their enum polarity is statically derived, not
confirmed on hardware. `roof` is additionally SAFETY-SENSITIVE: it physically
moves the pop-up roof and needs a ~1 Hz move-heartbeat, so it runs via
`device.actuate_roof` (re-send move at 1 Hz, bounded, always STOP) rather than the
one-shot `device.actuate`. Nothing runs automatically.
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
        # UNVERIFIED targets (not installed on this van) — interp keys per semantics.py.
        ("roofaircondition", "power"):       lambda: ("on", interp.get("on"), on),
        ("roofaircondition", "fanspeed"):    lambda: ("fan_speed", interp.get("fan_speed"), int(value)),
        ("roofaircondition", "mode"):        lambda: ("mode", interp.get("mode"), int(value)),
        ("roofaircondition", "temperature"): lambda: ("target_temp", interp.get("target_temp"), int(value)),
        ("stairs", "move"):     lambda: ("extended", interp.get("extended"),
                                         {"extend": True, "retract": False}.get(str(value).strip().lower())),
        ("stairs", "mode"):     lambda: ("mode", interp.get("mode"), int(value)),
        ("livingroomheater", "air"):         lambda: ("air_on", interp.get("air_on"), on),
        ("livingroomheater", "water"):       lambda: ("water_on", interp.get("water_on"), on),
        ("livingroomheater", "temperature"): lambda: ("air_temp", interp.get("air_temp"), int(value)),
    }
    fn = table.get((function, what))
    return fn() if fn else (what, None, None)


def _max_zone(interp):
    return max((interp.get("brightness_zone_%d" % z) or 0 for z in range(1, 17)), default=0)


async def _set_roof(f, funcs, dev, args):
    """Actuate the pop-up roof. SAFETY-SENSITIVE + NOT-LIVE-VERIFIED.

    `set roof open|close|stop`: the direction is `args.what`. open/close run the
    1 Hz move-heartbeat (`device.actuate_roof`, bounded then always STOP); stop is a
    one-shot STOP frame. Loudly flags that roof travel is unverified on this vehicle
    (roof not installed here) before writing."""
    from . import control
    from . import device as _device
    direction = args.what
    if direction not in control.ROOF_MOVES:
        print("roof direction must be open|close|stop, got %r" % direction, file=sys.stderr)
        return 2
    print("*** SAFETY-SENSITIVE + NOT-LIVE-VERIFIED ***", file=sys.stderr)
    print("roof %s: pop-up roof actuation. Not installed on this van -> enum polarity + "
          "SafetyCounter handshake are UNVERIFIED; this has never moved real hardware. "
          "Ensure the roof path is physically clear." % direction, file=sys.stderr)
    stop_frame = control.roof_frame(funcs, "stop")
    if direction == "stop":
        print("writing one-shot STOP %s (heartbeat-armed) ..." % stop_frame.hex())
        post = await dev.actuate(f, stop_frame, verify=True)
    else:
        move_frame = control.roof_frame(funcs, direction)
        print("writing roof %s: move %s re-sent at ~1 Hz for <= %.0fs then STOP %s ..."
              % (direction, move_frame.hex(), _device.ROOF_MAX_TRAVEL_S, stop_frame.hex()))
        post = await dev.actuate_roof(f, move_frame, stop_frame, verify=True)
    if post is None:
        print("write sent, no readback"); return 1
    print("after write: roof state = %s"
          % json.dumps(semantics.interpret("roof", post), default=str))
    return 0


async def cmd_set(funcs, dev, args):
    from . import control
    fn = args.function
    if fn not in control.BUILDERS:
        print("set not implemented for %r (have: %s)"
              % (fn, ", ".join(sorted(control.BUILDERS))), file=sys.stderr)
        return 2
    f = funcs[fn]
    if fn == "roof":
        return await _set_roof(f, funcs, dev, args)
    if args.value is None:
        print("set %s %s needs a value" % (fn, args.what), file=sys.stderr); return 2
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
    s.add_argument("function"); s.add_argument("what")
    # value is optional: `set roof open|close|stop` takes no value (the direction is `what`).
    s.add_argument("value", nargs="?", default=None)
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

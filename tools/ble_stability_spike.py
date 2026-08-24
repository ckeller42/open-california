"""Timeboxed spike: can buspi hold a PERSISTENT BLE session to the camper unit (like the phone
app) — long enough to keep the unit awake and get FRESH sensor values?

Context (docs/business-logic/value-freshness.md, DECISIONS): a plain poll returns a stale latched
value, and the unit drops buspi's held link after ~15-25 s, while the phone holds a minutes-long
iOS link that keeps the unit measuring. This tool measures, on real hardware, whether a
reconnect-on-drop + continuous-heartbeat loop can sustain an effectively-continuous session and
whether the fresh-water value climbs from its stale ~1 L to the true value while it runs.

RUN IT ONLY WHILE THE VAN IS AWAKE (ignition on / just used) and the phone app is closed, on buspi:

    PYTHONPATH=/home/pi/open-california python3 -m tools.ble_stability_spike --seconds 180

Reports per 5 s tick: connected?, water fresh level, reconnect count, RSSI; then a summary
(uptime %, drop count, whether water ever refreshed). This is a diagnostic — it makes no writes
other than the 1003 liveness heartbeat (read-only w.r.t. actuation), same as the app.
"""
from __future__ import annotations

import argparse
import asyncio
import time

from calictl import device, overrides, protocol


async def run(seconds: float, tick: float = 5.0) -> None:
    f = protocol.load(); overrides.apply(f)
    wc = f["water"].state_char
    dev = device.CamperDevice()
    stats = {"ticks": 0, "connected": 0, "reconnects": 0, "fresh_seen": None, "levels": []}

    async def connect_sub():
        c = await dev._session()
        try:
            await dev._subscribe_all(c)
        except Exception:
            pass
        return c

    client = await connect_sub()
    print("connected; holding %ds (heartbeat + reconnect-on-drop)" % seconds, flush=True)
    stop = asyncio.Event()
    beat = asyncio.ensure_future(dev._heartbeat(client, stop))
    t0 = time.time()
    while time.time() - t0 < seconds:
        stats["ticks"] += 1
        try:
            raw = await client.read_gatt_char(wc)
            lvl = protocol.decode(f["water"], bytes(raw))["FreshWaterLevel"]
            stats["connected"] += 1; stats["levels"].append(lvl)
            if lvl > 1 and stats["fresh_seen"] is None:
                stats["fresh_seen"] = round(time.time() - t0, 1)
            print("t=%3ds  connected=Y  water=%s  reconnects=%d"
                  % (time.time() - t0, lvl, stats["reconnects"]), flush=True)
        except Exception as e:
            print("t=%3ds  connected=N (%s) -> reconnecting" % (time.time() - t0, type(e).__name__),
                  flush=True)
            stop.set()
            try: await beat
            except Exception: pass
            try: await dev._safe_disconnect(client)
            except Exception: pass
            try:
                client = await connect_sub(); stats["reconnects"] += 1
                stop = asyncio.Event(); beat = asyncio.ensure_future(dev._heartbeat(client, stop))
            except Exception as e2:
                print("   reconnect failed: %s" % type(e2).__name__, flush=True)
        await asyncio.sleep(tick)
    stop.set()
    try: await beat
    except Exception: pass
    try: await dev._safe_disconnect(client)
    except Exception: pass

    up = stats["connected"] / stats["ticks"] if stats["ticks"] else 0
    print("\n=== SUMMARY ===", flush=True)
    print("uptime: %d/%d ticks (%.0f%%)" % (stats["connected"], stats["ticks"], up * 100))
    print("reconnects (link drops): %d" % stats["reconnects"])
    print("water refreshed above 1 L:", "yes at t=%ss" % stats["fresh_seen"] if stats["fresh_seen"]
          else "NO — never refreshed while held")
    print("water levels seen:", sorted(set(stats["levels"])))
    print("\nverdict:", "buspi CAN sustain a useful session -> persistent-read mode is viable"
          if up > 0.8 and stats["fresh_seen"] else
          "buspi canNOT hold the unit awake -> intermittent access is inherent; rely on offline/hold-last")


def main(argv=None):
    ap = argparse.ArgumentParser(description="spike: can buspi hold a persistent camper-unit session")
    ap.add_argument("--seconds", type=float, default=180.0)
    ap.add_argument("--tick", type=float, default=5.0)
    asyncio.run(run(ap.parse_args(argv).seconds, ap.parse_args(argv).tick))


if __name__ == "__main__":
    main()

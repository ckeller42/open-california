#!/usr/bin/env python3
"""At-the-van, human-in-the-loop verification of the roof control path.

Everything calictl knows about the pop-top roof is protocol-faithful but NOT-LIVE-VERIFIED:
`actuate_roof` has never driven a real motor. This runner walks an owner standing at the van
through the checks that only a physical test can settle, and records the evidence.

WHAT IT VERIFIES
  1. OPEN + auto-stop at the limit  — roof rises and the stream ceases at the open position.
  2. CLOSE + auto-stop at the limit — roof lowers and the stream ceases at the closed position.
  3. Mid-move STOP interrupt        — a "stop" during travel halts the roof before the limit.
  4. Re-press debounce (GUI only)   — mashing the web-UI button doesn't stutter travel.

HOW IT TALKS TO THE VAN
  Only over the running daemon's web API (GET /api/state, POST /api/command, POST /api/session).
  It NEVER opens its own BLE connection — `serve` owns the single slot (project hard rule). Run it
  ON buspi against the local daemon (default http://localhost:8088) so the test survives a flaky
  tailnet mid-move.

  NB: the daemon holds the BLE lock for the whole move, so /api/state returns the *cached* last
  poll during travel — the live Position ramp is not visible here. This runner therefore records
  before/after interpreted state plus (best-effort) the daemon's own log lines for the move window
  (`journalctl -u calictl`), where the auto-stop prints "roof reached limit position N — ceasing".

SAFETY
  Roof motion is safety-sensitive. Every move requires a typed "YES" that the path is clear and you
  are watching, and a STOP frame is ALWAYS sent after each move and on any exit (Ctrl-C included).
  Requires ignition ON and the daemon started with --enable-writes; the runner checks both first.

USAGE
  python3 -m tools.van_roof_test                 # on buspi, against localhost:8088
  python3 -m tools.van_roof_test --url http://buspi:8088 --out roof-evidence.json
"""
from __future__ import annotations

import argparse
import json
import signal
import subprocess
import sys
import time
import urllib.error
import urllib.request
from datetime import UTC, datetime

MOVE_POLL_HZ = 2.0                 # after-move state polling cadence
DEFAULT_MOVE_TIMEOUT = 35.0        # a hair over the daemon's ROOF_MAX_TRAVEL_S cap (30 s)


# --- tiny HTTP client over the daemon web API --------------------------------

class Daemon:
    def __init__(self, base: str):
        self.base = base.rstrip("/")

    def state(self) -> dict:
        with urllib.request.urlopen(self.base + "/api/state", timeout=10) as r:
            return json.load(r)

    def post(self, path: str, body: dict) -> dict:
        data = json.dumps(body).encode()
        req = urllib.request.Request(self.base + path, data=data,
                                     headers={"Content-Type": "application/json"}, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=95) as r:   # a move can block ~30 s
                return json.load(r)
        except urllib.error.HTTPError as e:
            try:
                return json.load(e)                              # our API returns JSON on errors too
            except Exception:
                return {"error": "http_%d" % e.code}
        except Exception as e:
            # A STOP must never raise — the exit-path safety frame goes through here. Degrade any
            # transport error (connection refused, timeout, tailnet drop) to an error dict.
            return {"error": "transport", "detail": repr(e)}

    def command(self, function: str, what: str, value=None, confirm: bool = False) -> dict:
        return self.post("/api/command", {"function": function, "what": what,
                                          "value": value, "confirm": confirm})

    def roof_stop(self) -> dict:
        return self.command("roof", "stop", confirm=True)


# --- prompts -----------------------------------------------------------------

def ask(prompt: str) -> str:
    try:
        return input(prompt).strip()
    except EOFError:
        return ""


def confirm_clear(action: str) -> bool:
    print("\n  ⚠  %s" % action)
    return ask("     Path clear and you are watching the roof? Type YES to proceed: ") == "YES"


def verdict(question: str) -> dict:
    ans = ask("     %s [y/n]: " % question).lower()
    note = ask("     Note (optional): ")
    return {"pass": ans.startswith("y"), "answer": ans, "note": note}


# --- telemetry helpers -------------------------------------------------------

def roof_of(state: dict) -> dict:
    r = dict(state.get("roof") or {})
    return {"position": r.get("position"), "position_name": r.get("position_name"),
            "safety_valid": r.get("safety_valid"), "alert": r.get("alert")}


def poll_until(dae: Daemon, seconds: float) -> list[dict]:
    """Sample interpreted roof state for `seconds` (used AFTER a move — during a move the daemon's
    poll is blocked on the BLE lock and this returns the stale cache)."""
    out, t0 = [], time.time()
    while time.time() - t0 < seconds:
        try:
            out.append({"t": round(time.time() - t0, 2), **roof_of(dae.state())})
        except Exception as e:
            out.append({"t": round(time.time() - t0, 2), "error": repr(e)})
        time.sleep(1.0 / MOVE_POLL_HZ)
    return out


def journal_since(epoch: float) -> list[str]:
    """Best-effort: the daemon's own roof log lines since `epoch` — where the auto-stop, dead-man
    abort and STOP print. Empty if journalctl is unavailable / not permitted."""
    try:
        out = subprocess.run(
            ["journalctl", "-u", "calictl.service", "--since", "@%d" % int(epoch), "--no-pager"],
            capture_output=True, text=True, timeout=15)
        lines = [ln for ln in out.stdout.splitlines()
                 if "actuate_roof" in ln or "roof" in ln.lower()]
        return lines[-40:]
    except Exception:
        return []


# --- the moves ---------------------------------------------------------------

def do_move(dae: Daemon, direction: str, timeout: float) -> dict:
    """Issue a blocking roof move, then capture after-state + the daemon's log window. The command
    returns when the move ends (auto-stop at limit, dead-man abort, or the travel cap)."""
    before = roof_of(dae.state())
    t0 = time.time()
    print("     sending roof %s (blocks until the move ends)…" % direction)
    resp = dae.command("roof", direction, confirm=True)
    elapsed = round(time.time() - t0, 1)
    dae.roof_stop()                                  # belt-and-suspenders STOP
    after = poll_until(dae, 4.0)                      # let the daemon poll settle post-STOP
    return {"direction": direction, "before": before, "command_response": resp,
            "move_seconds": elapsed, "after_timeline": after,
            "after": after[-1] if after else None, "journal": journal_since(t0)}


def do_move_then_stop(dae: Daemon, direction: str, stop_after_s: float, timeout: float) -> dict:
    """Start a move on a background thread and send STOP mid-travel from the main thread — proves the
    lock-free `_roof_stop` interrupt halts the roof before it reaches the limit."""
    import threading
    result = {}
    before = roof_of(dae.state())
    t0 = time.time()

    def _drive():
        result["command_response"] = dae.command("roof", direction, confirm=True)

    th = threading.Thread(target=_drive, daemon=True)
    print("     sending roof %s, then STOP after %.1f s…" % (direction, stop_after_s))
    th.start()
    time.sleep(stop_after_s)
    stop_resp = dae.roof_stop()
    th.join(timeout=timeout)
    dae.roof_stop()                                  # ensure stopped
    after = poll_until(dae, 4.0)
    return {"direction": direction, "before": before, "stop_after_s": stop_after_s,
            "stop_response": stop_resp, "command_response": result.get("command_response"),
            "move_seconds": round(time.time() - t0, 1),
            "after": after[-1] if after else None, "journal": journal_since(t0)}


# --- preflight ---------------------------------------------------------------

def preflight(dae: Daemon) -> dict:
    print("== Preflight ==")
    try:
        st = dae.state()
    except Exception as e:
        sys.exit("  daemon /api/state unreachable at %s (%r) — is `serve` up?" % (dae.base, e))
    meta = st.get("_meta") or {}
    print("  online=%s  as_of=%s" % (meta.get("online"), meta.get("as_of")))
    roof, veh = st.get("roof") or {}, st.get("vehicle") or {}
    if not roof.get("installed"):
        sys.exit("  roof.installed is false — no pop-top on this unit; nothing to test.")
    print("  roof: position=%s (%s)  safety_valid=%s  alert=%s"
          % (roof.get("position"), roof.get("position_name"),
             roof.get("safety_valid"), roof.get("alert")))
    ign = veh.get("ignition_on")
    print("  ignition_on=%s" % ign)
    if not ign:
        print("  ⚠  ignition is OFF — the roof motor is gated on ignition; moves will not run.")
        if ask("     Turn the ignition ON now, then type READY (or SKIP to continue anyway): ") == "SKIP":
            pass
        else:
            st = dae.state(); ign = (st.get("vehicle") or {}).get("ignition_on")
            print("  ignition_on=%s" % ign)
    # write-enabled probe: a STOP is harmless (halts nothing when idle) but round-trips the write path.
    print("  probing write path (harmless STOP)…")
    probe = dae.roof_stop()
    if probe.get("error") == "read_only":
        sys.exit("  daemon is READ-ONLY. On buspi, run `bash tools/roof_test_session.sh` instead — it\n"
                 "  enables writes for the test and reverts to read-only on exit. (Or restart serve\n"
                 "  with --enable-writes / CALICTL_ENABLE_WRITES=1.)")
    print("  write path OK (%s)" % json.dumps(probe))
    print("  warming BLE session…", dae.post("/api/session", {"action": "connect"}))
    return {"online": meta.get("online"), "as_of": meta.get("as_of"),
            "roof": roof_of(st), "ignition_on": ign}


# --- main --------------------------------------------------------------------

def main() -> None:
    ap = argparse.ArgumentParser(description="At-the-van roof verification via the calictl daemon.")
    ap.add_argument("--url", default="http://localhost:8088", help="daemon web API base URL")
    ap.add_argument("--out", default=None, help="evidence JSON path (default van-roof-test-<ts>.json)")
    ap.add_argument("--move-timeout", type=float, default=DEFAULT_MOVE_TIMEOUT)
    args = ap.parse_args()

    dae = Daemon(args.url)
    ts = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    out_path = args.out or "van-roof-test-%s.json" % ts
    evidence: dict = {"started": ts, "url": args.url, "checks": []}

    # ALWAYS stop the roof on the way out, however we exit.
    def _panic(*_a):
        try:
            dae.roof_stop()
        finally:
            print("\n  STOP sent on exit.")
            sys.exit(1)
    signal.signal(signal.SIGINT, _panic)
    signal.signal(signal.SIGTERM, _panic)

    print("\n=== calictl at-the-van roof test ===")
    print("Roof control is SAFETY-SENSITIVE and has never driven a real motor. Stop any time with"
          " Ctrl-C (a STOP frame is sent).\n")
    try:
        evidence["preflight"] = preflight(dae)

        # 1) OPEN + auto-stop
        print("\n== Check 1: OPEN + auto-stop at the limit ==")
        if confirm_clear("The roof will RISE. Keep hands and obstructions clear."):
            r = do_move(dae, "open", args.move_timeout)
            print("     move ended after %ss; roof now: %s" % (r["move_seconds"], r["after"]))
            for ln in r["journal"]:
                print("       | " + ln)
            r["verdict"] = verdict("Did the roof rise fully and STOP on its own at the top?")
            evidence["checks"].append({"check": "open_auto_stop", **r})

        # 2) CLOSE + auto-stop
        print("\n== Check 2: CLOSE + auto-stop at the limit ==")
        if confirm_clear("The roof will LOWER. Keep hands and obstructions clear."):
            r = do_move(dae, "close", args.move_timeout)
            print("     move ended after %ss; roof now: %s" % (r["move_seconds"], r["after"]))
            for ln in r["journal"]:
                print("       | " + ln)
            r["verdict"] = verdict("Did the roof lower fully and STOP on its own when closed?")
            evidence["checks"].append({"check": "close_auto_stop", **r})

        # 3) Mid-move STOP interrupt
        print("\n== Check 3: mid-move STOP interrupt ==")
        if confirm_clear("The roof will start to RISE, then STOP after ~2.5 s (partway)."):
            r = do_move_then_stop(dae, "open", 2.5, args.move_timeout)
            print("     roof after interrupt: %s" % r["after"])
            for ln in r["journal"]:
                print("       | " + ln)
            r["verdict"] = verdict("Did the roof HALT partway (not reach the top) when STOP was sent?")
            evidence["checks"].append({"check": "mid_move_stop", **r})
            # leave the roof closed for a tidy finish
            if confirm_clear("Now CLOSING the roof to finish."):
                fin = do_move(dae, "close", args.move_timeout)
                evidence["checks"].append({"check": "close_after_interrupt", **fin})

        # 4) Re-press debounce (GUI only — client-side in app.js, can't be driven via the API)
        print("\n== Check 4: re-press debounce (web UI) ==")
        print("  In the web UI, press-and-hold OPEN, release, and immediately mash OPEN a few times.")
        print("  The 1000 ms debounce should ignore the too-quick re-presses (no stutter/restart).")
        if ask("  Run this GUI check now? [y/n]: ").lower().startswith("y"):
            evidence["checks"].append({"check": "repress_debounce_gui",
                                       "verdict": verdict("Did rapid re-presses NOT stutter/restart travel?")})
    finally:
        dae.roof_stop()
        with open(out_path, "w") as f:
            json.dump(evidence, f, indent=2)
        print("\n=== Summary ===")
        for c in evidence.get("checks", []):
            v = c.get("verdict") or {}
            mark = "PASS" if v.get("pass") else ("FAIL" if v else "-")
            print("  [%s] %-24s %s" % (mark, c["check"], v.get("note", "")))
        print("\nEvidence written to %s" % out_path)
        print("Paste confirmed results into docs/business-logic/evidence-ledger.md "
              "(roof rows: DECOMPILE → DEVICE).")


if __name__ == "__main__":
    main()

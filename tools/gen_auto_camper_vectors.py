#!/usr/bin/env python3
"""Generate ``tests/vectors/auto_camper.json`` — calictl's restore-after-park decision as golden vectors.

Every case is one call of :func:`calictl.automation.auto_camper_restore_decide` (the authority): the
inputs, the carried state, and what the function returned. The C twin
(``firmware/components/cali_core/auto_camper.c``) must reproduce every case
(``tests/firmware/test_auto_camper_parity.py``). The clock is ms (the ESP's uptime) — the function only
compares ``now`` with ``restore_until``. Checked in; never hand-edit:

    python3 -m tools.gen_auto_camper_vectors            # rewrite the file
    python3 -m tools.gen_auto_camper_vectors --check    # exit 1 if the checked-in file is stale
"""

import itertools
import json
import random
import sys
from pathlib import Path

from calictl import automation

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "tests" / "vectors" / "auto_camper.json"

NOW = 1_000_000
CFG = {
    "min_soc": automation.AUTO_CAMPER_MIN_SOC,
    "max_fails": automation.AUTO_CAMPER_MAX_FAILS,
    "window": 120_000,
}
TRI = (None, False, True)
UNTIL = (None, NOW + 5000, NOW, NOW - 1)
PRE = (None, (False, False), (True, False), (False, True), (True, True))


def _case(i, ign, master, usb, lights, soc, warning, owe, pre, until, fails, pign, pm, pusb, plights):
    d = automation.auto_camper_restore_decide(
        ignition_on=ign,
        prev_ignition=pign,
        master_on=master,
        prev_master=pm,
        usb_on=usb,
        lights_on=lights,
        prev_usb=pusb,
        prev_lights=plights,
        soc=soc,
        warning=warning,
        now=NOW,
        owe_restore=owe,
        pre_drive=None if pre is None else {"master": True, "usb": pre[0], "lights": pre[1]},
        restore_until=until,
        fails=fails,
        min_soc=CFG["min_soc"],
        max_fails=CFG["max_fails"],
        window_s=CFG["window"],
    )
    pd = d["pre_drive"]
    if d["actuate"]:
        assert d["restore_config"] is pd  # the C twin restores its state's pre_drive
    return {
        "id": i,
        "in": [ign, master, usb, lights, soc, warning],
        "state": [owe, None if pre is None else list(pre), until, fails, pign, pm, pusb, plights],
        "out": [
            d["actuate"],
            d["restored"],
            d["notice"],
            d["owe_restore"],
            None if pd is None else [pd["usb"], pd["lights"]],
            d["restore_until"],
            d["fails"],
            d["prev_ignition"],
            d["prev_master"],
            d["prev_usb"],
            d["prev_lights"],
        ],
    }


def generate():
    cases = []
    # every edge x debt combination, battery healthy
    for ign, master, pign, pm, owe, until, fails in itertools.product(
        TRI, TRI, TRI, TRI, (False, True), UNTIL, (0, 3)
    ):
        cases.append(
            _case(
                len(cases),
                ign,
                master,
                True,
                False,
                80,
                False,
                owe,
                (True, False),
                until,
                fails,
                pign,
                pm,
                True,
                True,
            )
        )
    # the battery guard and the restore payload, seeded random over the rest
    rnd = random.Random(154)
    for _ in range(600):
        cases.append(
            _case(
                len(cases),
                rnd.choice(TRI),
                rnd.choice(TRI),
                rnd.choice(TRI),
                rnd.choice(TRI),
                rnd.choice((None, 0, 10, 19, 20, 100)),
                rnd.choice((False, True)),
                rnd.choice((False, True)),
                rnd.choice(PRE),
                rnd.choice(UNTIL),
                rnd.choice((0, 1, 2, 3, 4)),
                rnd.choice(TRI),
                rnd.choice(TRI),
                rnd.choice(TRI),
                rnd.choice(TRI),
            )
        )
    return {"now": NOW, "cfg": CFG, "cases": cases}


def render() -> str:
    """The exact checked-in file content: one case per line."""
    g = generate()
    head = json.dumps({"now": g["now"], "cfg": g["cfg"]})[:-1]
    body = ",\n".join(json.dumps(c, ensure_ascii=False) for c in g["cases"])
    return head + ', "cases": [\n' + body + "\n]}\n"


def main() -> int:
    text = render()
    if "--check" in sys.argv:
        if not OUT.is_file() or OUT.read_text(encoding="utf-8") != text:
            print("STALE: %s — run python3 -m tools.gen_auto_camper_vectors" % OUT, file=sys.stderr)
            return 1
        print("fresh: %s" % OUT)
        return 0
    OUT.write_text(text, encoding="utf-8")
    print("wrote %s (%d cases)" % (OUT, len(generate()["cases"])))
    return 0


if __name__ == "__main__":
    sys.exit(main())

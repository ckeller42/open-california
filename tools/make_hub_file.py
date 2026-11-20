"""Print the remote-hub setup file (``oc-hub.json``) for the satellite's Hub form (issue #555).

The wizard's Remote access step and the Hub card have a "Load from file" button: it reads this file on
the phone and fills the six fields; the owner taps Save. The keys are exactly those of ``POST /api/hub``.

The values come from the environment (the owner's ``~/.env``), never from the command line, so no
password lands in the shell history. The file holds the van password: keep it only until the form is
saved, then delete it. Usage::

    python3 -m tools.make_hub_file --url wss://<id>.ala.<region>.emqxsl.com:8084/mqtt > oc-hub.json

Environment: OC_HUB_VAN, OC_HUB_VAN_USER, OC_HUB_VAN_PASS, OC_HUB_APP_USER, OC_HUB_APP_PASS
(and OC_HUB_URL when --url is not given).
"""

import argparse
import json
import os
import sys

FORMAT = "oc-hub/1"
ENV = {"van": "OC_HUB_VAN", "user": "OC_HUB_VAN_USER", "pass": "OC_HUB_VAN_PASS",
       "app_user": "OC_HUB_APP_USER", "app_pass": "OC_HUB_APP_PASS"}


def build(url: str, env: dict, lora: bool = False) -> dict:
    """The file's object; raises ValueError for a missing value or one the firmware refuses."""
    if not url.startswith("wss://"):
        raise ValueError("the broker address must start with wss://")
    out = {"format": FORMAT, "url": url}
    for key, var in ENV.items():
        if not env.get(var):
            raise ValueError(f"{var} is not set")
        out[key] = env[var]
    if any("*" in v for v in out.values()):  # the hub QR's separator (firmware answers 400)
        raise ValueError("no value may contain *")
    if lora:  # Save also turns the satellite's LoRa switch on (its keys are made on the board)
        out["lora"] = True
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--url", default=os.environ.get("OC_HUB_URL", ""), help="wss://…:8084/mqtt")
    ap.add_argument("--lora", action="store_true", help="Save also turns the LoRa switch on")
    a = ap.parse_args()
    try:
        print(json.dumps(build(a.url, dict(os.environ), a.lora), indent=2))
    except ValueError as e:
        print(f"make_hub_file: {e}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())

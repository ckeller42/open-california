"""Print the remote-hub setup file (``oc-hub.json``) for the satellite's Hub form (issue #555).

The wizard's Remote access step and the Hub card have a "Load from file" button: it reads this file on
the phone and fills the six fields; the owner taps Save. The keys are exactly those of ``POST /api/hub``.

The values come from the environment (the owner's ``~/.env``), never from the command line, so no
password lands in the shell history. The file holds the van password: keep it only until the form is
saved, then delete it. Usage::

    python3 -m tools.make_hub_file --url wss://<id>.ala.<region>.emqxsl.com:8084/mqtt > oc-hub.json

Environment: OC_HUB_VAN, OC_HUB_VAN_USER, OC_HUB_VAN_PASS, OC_HUB_APP_USER, OC_HUB_APP_PASS
(and OC_HUB_URL when --url is not given).

``--lora`` adds ``"lora": true``: the satellite's Save also turns its LoRa switch on. ``--ttn-cluster HOST``
adds ``"ttn": {"cluster", "key"}`` for the remote app's "Set up LoRa" (its own "Load from file"); the key is a
short-lived application key with all rights from OC_HUB_TTN_KEY. The app deletes it after the setup.
"""
import re

import argparse
import json
import os
import sys

FORMAT = "oc-hub/1"
#: remote.js remoteHostOk: a plain TTS host under the two domains the app's CSP allows.
TTN_HOST_RE = re.compile(r"([a-z0-9-]+\.)+thethings\.(network|industries)")
TTN_KEY_RE = re.compile(r"NNSXS\.[A-Z0-9]+\.[A-Z0-9]+")
ENV = {"van": "OC_HUB_VAN", "user": "OC_HUB_VAN_USER", "pass": "OC_HUB_VAN_PASS",
       "app_user": "OC_HUB_APP_USER", "app_pass": "OC_HUB_APP_PASS"}


def build(url: str, env: dict, lora: bool = False, ttn_cluster: str = "") -> dict:
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
    if ttn_cluster:  # the remote app's Set up LoRa: cluster + a full-rights application key it deletes after use
        if len(ttn_cluster) > 64 or not TTN_HOST_RE.fullmatch(ttn_cluster):
            raise ValueError("the TTN cluster must be a host like eu1.cloud.thethings.network")
        if not TTN_KEY_RE.fullmatch(env.get("OC_HUB_TTN_KEY", "")):
            raise ValueError("OC_HUB_TTN_KEY is not set or not a TTN API key")
        out["ttn"] = {"cluster": ttn_cluster, "key": env["OC_HUB_TTN_KEY"]}
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--url", default=os.environ.get("OC_HUB_URL", ""), help="wss://…:8084/mqtt")
    ap.add_argument("--lora", action="store_true", help="Save also turns the LoRa switch on")
    ap.add_argument("--ttn-cluster", default="", help="TTS host for the app's Set up LoRa (key: OC_HUB_TTN_KEY)")
    a = ap.parse_args()
    try:
        print(json.dumps(build(a.url, dict(os.environ), a.lora, a.ttn_cluster), indent=2))
    except ValueError as e:
        print(f"make_hub_file: {e}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())

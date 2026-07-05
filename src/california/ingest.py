from __future__ import annotations

import json
import os
import shutil
import subprocess

from california.model import Capture, parse_label
from california.parse import parse_tshark

_DISPLAY_FILTER = "btatt || btrfcomm || btspp"


def run_tshark(path: str, tshark_bin: str = "tshark") -> list[dict]:
    if shutil.which(tshark_bin) is None:
        raise FileNotFoundError(f"{tshark_bin} not found; install Wireshark CLI")
    proc = subprocess.run(
        [tshark_bin, "-r", path, "-T", "json", "-Y", _DISPLAY_FILTER],
        capture_output=True,
        text=True,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"tshark failed ({proc.returncode}): {proc.stderr.strip()}")
    return json.loads(proc.stdout or "[]")


def load_capture(path: str) -> Capture:
    date, action, index = parse_label(os.path.basename(path))
    packets = run_tshark(path)
    return Capture(action=action, index=index, date=date, events=parse_tshark(packets))

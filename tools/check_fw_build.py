"""Check an ESP-IDF build of the satellite before anything flashes it or ships it over OTA (R_FW_OTA).

    python3 tools/check_fw_build.py [BUILD_DIR] [--sha GIT_SHA]

``BUILD_DIR`` is a release build of ``firmware/`` (default ``firmware/build``). CI's ``firmware-build``
runs it after ``idf.py build``; locally run it after the podman build (docs/firmware.md "CI jobs").
Checks, each against the files the build wrote:

- the resolved config (``config/sdkconfig.json``) holds every line of ``firmware/sdkconfig.defaults``:
  a symbol an IDF or component bump renamed or that a dependency forces off is otherwise dropped with
  only a warning, and a stale local ``firmware/sdkconfig`` wins over the defaults; rollback is on;
- the built partition table (the binary that is flashed) equals ``firmware/partitions.csv``;
- ``flasher_args.json`` (what flash.sh / flash_ci.sh write): the flash size holds the table, the
  bootloader and the checked table sit at 0x0 / 0x8000, the otadata and app images at their
  partitions' offsets, no image touches ``nvs``/``phy_init``, and no ``--erase-all``;
- the app fits its OTA slot with 10 % to spare;
- with ``--sha``: the image's version (``esp_app_desc_t``) is that commit's prefix, not ``-dirty``
  (the OTA check compares it with the release manifest's sha).

Exit 0 when every check holds, 1 with one line per failure. With ``GITHUB_STEP_SUMMARY`` set, the
report is appended there too.
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import struct
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FW = ROOT / "firmware"
SLOT_MARGIN = 0.9  # the app may fill at most 90 % of its OTA slot
TYPES = {"app": 0, "data": 1}
SUBTYPES = {"ota_0": 0x10, "ota_1": 0x11, "ota": 0, "phy": 1, "nvs": 2}


def config_mismatches(defaults: str, resolved: dict) -> list[str]:
    """Lines of ``sdkconfig.defaults`` that the resolved config (sdkconfig.json) does not hold."""
    bad = []
    for ln in defaults.splitlines():
        if not ln.startswith("CONFIG_") or "=" not in ln:
            continue
        key, want = ln.split("=", 1)
        got = resolved.get(key[len("CONFIG_") :])
        if want == "y":
            ok = got is True
        elif want == "n":
            ok = got in (False, None)  # an off bool whose menu is hidden is simply absent
        elif want.startswith('"'):
            ok = got == json.loads(want)
        else:
            ok = isinstance(got, int) and not isinstance(got, bool) and got == int(want, 0)
        if not ok:
            bad.append(f"config: {ln} not in the build (resolved: {got!r})")
    return bad


def csv_table(text: str) -> list[tuple]:
    """``partitions.csv`` -> [(name, type, subtype, offset, size)]."""
    out = []
    for r in csv.reader(text.splitlines()):
        if r and r[0].strip() and not r[0].startswith("#"):
            name, typ, sub, off, size = (c.strip() for c in r[:5])
            out.append((name, TYPES[typ], SUBTYPES[sub], int(off, 0), int(size, 0)))
    return out


def bin_table(data: bytes) -> list[tuple]:
    """The flashed partition table (32-byte entries, magic 0x50AA; the MD5 entry is 0xEBEB)."""
    out = []
    for i in range(0, len(data), 32):
        magic, typ, sub, off, size, label = struct.unpack_from("<HBBII16s", data, i)
        if magic != 0x50AA:
            break
        out.append((label.rstrip(b"\0").decode(), typ, sub, off, size))
    return out


def app_version(image: bytes) -> str:
    """esp_app_desc_t.version: the first segment of the app image, after the 24 + 8 header bytes."""
    magic, _secure_version, _reserved, ver = struct.unpack_from("<II8s32s", image, 32)
    return ver.split(b"\0")[0].decode() if magic == 0xABCD5432 else "<no app desc>"


def check(build: Path, sha: str | None = None) -> tuple[list[str], list[str]]:
    """Run every check on ``build``. Returns (failures, info lines)."""
    bad: list[str] = []
    info: list[str] = []
    resolved = json.loads((build / "config" / "sdkconfig.json").read_text())
    bad += config_mismatches((FW / "sdkconfig.defaults").read_text(), resolved)
    if resolved.get("BOOTLOADER_APP_ROLLBACK_ENABLE") is not True:
        bad.append("config: CONFIG_BOOTLOADER_APP_ROLLBACK_ENABLE is off: a bad OTA image cannot roll back")

    table = csv_table((FW / "partitions.csv").read_text())
    flashed = bin_table((build / "partition_table" / "partition-table.bin").read_bytes())
    if flashed != table:
        bad.append(f"partition table: the built table {flashed} differs from partitions.csv {table}")
    part = {p[0]: p for p in table}

    fa = json.loads((build / "flasher_args.json").read_text())
    size_mb = int(fa["flash_settings"]["flash_size"].removesuffix("MB"))
    end = max(p[3] + p[4] for p in table)
    if end > size_mb << 20:
        bad.append(f"flasher_args: flash size {size_mb} MB is smaller than the table's end {end:#x}")
    files = {int(off, 16): f for off, f in fa["flash_files"].items()}
    erase = {"--erase-all", "--erase_all", "-e"} & set(fa.get("write_flash_args", []))
    if erase:
        bad.append(f"flasher_args: write_flash_args has {sorted(erase)}: a USB flash would erase NVS")
    for off, want in ((0x0, "bootloader/bootloader.bin"), (0x8000, "partition_table/partition-table.bin")):
        if files.get(off) != want:
            bad.append(f"flasher_args: {off:#x} maps to {files.get(off)!r}, not {want}")
    for role, name in (("otadata", "otadata"), ("app", "ota_0")):
        off = int(fa[role]["offset"], 16)
        if name not in part or off != part[name][3] or files.get(off) != fa[role]["file"]:
            bad.append(
                f"flasher_args: {role} at {off:#x}, partitions.csv has {name} at {part.get(name, (0,) * 4)[3]:#x}"
            )
    for off, f in sorted(files.items()):
        path = build / f
        if not path.is_file():
            bad.append(f"flasher_args: {f} is missing")
            continue
        stop = off + path.stat().st_size
        for keep in ("nvs", "phy_init"):
            p = part.get(keep)
            if p and off < p[3] + p[4] and p[3] < stop:
                bad.append(
                    f"flasher_args: {f} ({off:#x}-{stop:#x}) overwrites {keep}: a USB flash would erase it"
                )

    app = build / fa["app"]["file"]
    slot = min(p[4] for p in table if p[1] == TYPES["app"])
    n = app.stat().st_size
    info.append(f"app {n} B = {n * 100 // slot} % of the OTA slot ({slot} B)")
    if n > slot * SLOT_MARGIN:
        bad.append(f"app: {app.name} uses more than {SLOT_MARGIN:.0%} of the OTA slot")

    ver = app_version(app.read_bytes())
    info.append(f"version {ver}")
    if sha and (not sha.startswith(ver) or len(ver) < 7):
        bad.append(
            f"version: the image says {ver!r}, the commit is {sha}: the OTA check would never read up_to_date"
        )
    return bad, info


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("build", nargs="?", default=str(FW / "build"))
    ap.add_argument("--sha", help="the commit the image must report as its version")
    a = ap.parse_args(argv)
    bad, info = check(Path(a.build), a.sha)
    lines = info + [f"FAIL {b}" for b in bad] + ([] if bad else ["check_fw_build: ok"])
    print("\n".join(lines))
    if os.environ.get("GITHUB_STEP_SUMMARY"):
        with open(os.environ["GITHUB_STEP_SUMMARY"], "a") as f:
            f.write("\n".join(lines) + "\n")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())

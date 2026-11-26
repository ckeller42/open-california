"""Guards on the repo files that decide what a flash or an OTA writes to the satellite (R_FW_OTA).

An ESP32-S3 board takes its bootloader and partition table only from a USB flash. An OTA writes the
app slot and nothing else, so a board that was updated over OTA keeps the table and the bootloader
of its last USB flash. These tests read the committed files (no ESP-IDF needed) and fail on the
changes that can leave a board in a boot loop, with an image that cannot roll back, or with a
silently different build. The checks on the build output are in ``tools/check_fw_build.py``.

.. test:: The committed partition table, sdkconfig, component pins and release gate keep a flash or OTA safe
   :id: T_FW_FLASH_SAFETY_REPO
   :links: R_FW_OTA

.. test:: The build-output check fails on a dropped config line, a wrong flash layout, an NVS overwrite or an erase-all
   :id: T_FW_FLASH_SAFETY_BUILD
   :links: R_FW_OTA
"""

import csv
import json
import re
import struct
import subprocess
from pathlib import Path

import pytest

yaml = pytest.importorskip("yaml")

ROOT = Path(__file__).resolve().parents[1]
FW = ROOT / "firmware"


def _parts():
    rows = [
        r
        for r in csv.reader((FW / "partitions.csv").read_text().splitlines())
        if r and not r[0].startswith("#")
    ]
    return {
        r[0].strip(): {"type": r[1].strip(), "sub": r[2].strip(), "off": int(r[3], 0), "size": int(r[4], 0)}
        for r in rows
    }


def _config(path):
    return dict(
        ln.split("=", 1) for ln in path.read_text().splitlines() if ln.startswith("CONFIG_") and "=" in ln
    )


# The layout every board in the field runs. An OTA never rewrites the partition table, so an app built
# for another layout would run against this one. Changing it needs a USB flash of every board first
# (flash_ci.sh) and NVS/otadata offsets that keep the bonds; update this table only then.
FIELD_LAYOUT = {
    "nvs": (0x9000, 0x6000),
    "phy_init": (0xF000, 0x1000),
    "otadata": (0x10000, 0x2000),
    "ota_0": (0x20000, 0x300000),
    "ota_1": (0x320000, 0x300000),
}


def test_partition_table_is_the_field_layout():
    parts = _parts()
    assert {k: (v["off"], v["size"]) for k, v in parts.items()} == FIELD_LAYOUT, (
        "partitions.csv changed: boards updated over OTA keep their old table (see FIELD_LAYOUT)"
    )


def test_partition_table_fits_the_flash_and_the_ota_slots_match():
    parts = _parts()
    flash_mb = int(
        re.search(r"^CONFIG_ESPTOOLPY_FLASHSIZE_(\d+)MB=y$", (FW / "sdkconfig.defaults").read_text(), re.M)[1]
    )
    spans = sorted((p["off"], p["off"] + p["size"]) for p in parts.values())
    assert all(a_end <= b_off for (_, a_end), (b_off, _) in zip(spans, spans[1:])), "partitions overlap"
    assert spans[0][0] >= 0x9000, "a partition overlaps the bootloader / partition table (0x0-0x8fff)"
    assert spans[-1][1] <= flash_mb << 20, "the table ends beyond the flash size"
    apps = [p for p in parts.values() if p["type"] == "app"]
    assert sorted(p["sub"] for p in apps) == ["ota_0", "ota_1"]  # rollback needs two slots, no factory
    assert len({p["size"] for p in apps}) == 1, "an image that fits one slot must fit the other"
    assert all(p["off"] % 0x10000 == 0 for p in apps), "app partitions must be 64 KB aligned"


def test_docs_partition_table_matches_partitions_csv():
    doc = (ROOT / "docs" / "firmware.md").read_text()
    rows = dict(
        (m[1], (int(m[2], 16), int(m[3], 16)))
        for m in re.finditer(r"^\| `(\w+)`[^|]*\| (0x[0-9a-f]+) \| (0x[0-9a-f]+) \|$", doc, re.M)
    )
    assert rows == {k: (v["off"], v["size"]) for k, v in _parts().items()}


def test_sdkconfig_keeps_rollback_partition_table_and_flash_size():
    """Rollback is a bootloader option: an image that turns it off still boots, but the next bad OTA has
    no way back. The QEMU overlay must not turn off what the release image relies on."""
    rel = _config(FW / "sdkconfig.defaults")
    for key, val in {
        "CONFIG_BOOTLOADER_APP_ROLLBACK_ENABLE": "y",
        "CONFIG_PARTITION_TABLE_CUSTOM": "y",
        "CONFIG_PARTITION_TABLE_CUSTOM_FILENAME": '"partitions.csv"',
        "CONFIG_ESPTOOLPY_FLASHSIZE_16MB": "y",
    }.items():
        assert rel.get(key) == val, key
    qemu = _config(FW / "qemu" / "sdkconfig.qemu")
    assert not any(
        k.startswith(("CONFIG_BOOTLOADER_", "CONFIG_PARTITION_TABLE_", "CONFIG_ESPTOOLPY_")) for k in qemu
    )


def test_generated_sdkconfig_is_not_tracked():
    """``firmware/sdkconfig`` overrides ``sdkconfig.defaults`` once it exists: a committed one would make
    every build ignore later changes to the defaults."""
    tracked = subprocess.run(
        ["git", "ls-files", "firmware/sdkconfig", "firmware/sdkconfig.old"],
        cwd=ROOT,
        capture_output=True,
        text=True,
    ).stdout
    assert tracked == ""


def test_component_pins_are_exact_and_in_step():
    """No ``dependencies.lock`` is committed, so each ``idf_component.yml`` pin is the only thing that
    fixes a dependency. A package pinned in two manifests to different versions makes the component
    manager pick one of them (or fail), not the one each component was written against."""
    pins: dict[str, dict[str, str]] = {}
    for f in sorted(FW.glob("components/*/idf_component.yml")) + sorted(FW.glob("main/idf_component.yml")):
        for name, spec in (yaml.safe_load(f.read_text()).get("dependencies") or {}).items():
            spec = spec.get("version") if isinstance(spec, dict) else spec
            if name == "idf":
                continue
            assert re.fullmatch(r"==\d+(\.\d+)*", str(spec)), (
                f"{f.relative_to(ROOT)}: {name} {spec!r} is not an exact pin"
            )
            pins.setdefault(name, {})[str(f.relative_to(ROOT))] = spec
    assert pins, "no idf_component.yml found"
    split = {n: by for n, by in pins.items() if len(set(by.values())) > 1}
    assert not split, f"one package, different pins: {split}"


def test_esp_latest_waits_for_the_qemu_boot():
    """``esp-latest`` is the OTA source of every board on the ``latest`` channel: an image that fails
    to boot in QEMU must not reach it (``esp-stable`` already needs a fully green main run)."""
    jobs = yaml.safe_load((ROOT / ".github" / "workflows" / "ci.yml").read_text())["jobs"]
    rel = jobs["firmware-release"]
    needs = rel["needs"] if isinstance(rel["needs"], list) else [rel["needs"]]
    assert {"firmware-build", "firmware-qemu"} <= set(needs)
    assert "refs/heads/main" in rel["if"] and "always()" not in rel["if"]
    build = "\n".join(s.get("run", "") for s in jobs["firmware-build"]["steps"])
    assert "tools/check_fw_build.py" in build


def _fake_build(tmp_path):
    """A minimal build directory as ``idf.py build`` writes it, made from the committed files."""
    from tools import check_fw_build as c

    resolved = {}
    for ln in (FW / "sdkconfig.defaults").read_text().splitlines():
        if ln.startswith("CONFIG_") and "=" in ln:
            k, v = ln[len("CONFIG_") :].split("=", 1)
            resolved[k] = (
                {"y": True, "n": False}.get(v) if v in "yn" else (v.strip('"') if v[0] == '"' else int(v, 0))
            )
    (tmp_path / "config").mkdir()
    (tmp_path / "config" / "sdkconfig.json").write_text(json.dumps(resolved))
    (tmp_path / "partition_table").mkdir()
    table = b"".join(
        struct.pack("<HBBII16sI", 0x50AA, t, s, o, n, name.encode(), 0)
        for name, t, s, o, n in c.csv_table((FW / "partitions.csv").read_text())
    )
    (tmp_path / "partition_table" / "partition-table.bin").write_bytes(table + b"\xff" * 32)
    (tmp_path / "bootloader").mkdir()
    (tmp_path / "bootloader" / "bootloader.bin").write_bytes(b"\xe9" * 0x5000)
    (tmp_path / "ota_data_initial.bin").write_bytes(b"\xff" * 0x2000)
    desc = struct.pack("<II8s32s", 0xABCD5432, 0, b"", b"b6fbdc2")
    (tmp_path / "cali_fw.bin").write_bytes(b"\xe9" + b"\0" * 31 + desc + b"\0" * 0x100000)
    files = {"0x0": "bootloader/bootloader.bin", "0x8000": "partition_table/partition-table.bin",
             "0x10000": "ota_data_initial.bin", "0x20000": "cali_fw.bin"}  # fmt: skip
    args = {
        "flash_settings": {"flash_size": "16MB"},
        "flash_files": files,
        "otadata": {"offset": "0x10000", "file": "ota_data_initial.bin"},
        "app": {"offset": "0x20000", "file": "cali_fw.bin"},
    }
    (tmp_path / "flasher_args.json").write_text(json.dumps(args))
    return c, args


def test_build_check_fails_on_what_breaks_a_board(tmp_path):
    """``tools/check_fw_build.py`` passes a correct build and names each fault in a broken one."""
    c, args = _fake_build(tmp_path)
    sha = "b6fbdc25251ae634fe952f39650f763e6509b28c"
    assert c.check(tmp_path, sha)[0] == []
    assert c.check(tmp_path, "0" * 40)[0][0].startswith("version:")

    cfg = tmp_path / "config" / "sdkconfig.json"
    good_cfg = cfg.read_text()
    cfg.write_text(json.dumps({**json.loads(good_cfg), "BOOTLOADER_APP_ROLLBACK_ENABLE": False}))
    assert any("ROLLBACK" in b for b in c.check(tmp_path)[0])
    resolved = json.loads(good_cfg)
    del resolved["SPIRAM_TRY_ALLOCATE_WIFI_LWIP"]  # a symbol the build dropped
    cfg.write_text(json.dumps(resolved))
    assert c.check(tmp_path)[0] == [
        "config: CONFIG_SPIRAM_TRY_ALLOCATE_WIFI_LWIP=y not in the build (resolved: None)"
    ]
    cfg.write_text(good_cfg)

    fa = tmp_path / "flasher_args.json"
    for broken, why in (
        ({**args, "flash_settings": {"flash_size": "4MB"}}, "flash size"),
        (
            {**args, "flash_files": {**args["flash_files"], "0x9000": "ota_data_initial.bin"}},
            "overwrites nvs",
        ),
        ({**args, "app": {"offset": "0x320000", "file": "cali_fw.bin"}}, "app at 0x320000"),
        ({**args, "write_flash_args": ["--flash-size", "16MB", "--erase-all"]}, "erase NVS"),
        (
            {**args, "flash_files": {**args["flash_files"], "0x8000": "ota_data_initial.bin"}},
            "0x8000 maps to",
        ),
    ):
        fa.write_text(json.dumps(broken))
        assert any(why in b for b in c.check(tmp_path)[0]), why
    fa.write_text(json.dumps(args))

    (tmp_path / "cali_fw.bin").write_bytes(b"\0" * 0x2D0000)  # > 90 % of a 3 MB slot
    assert any("OTA slot" in b for b in c.check(tmp_path)[0])
    pt = tmp_path / "partition_table" / "partition-table.bin"
    pt.write_bytes(pt.read_bytes()[32:])  # nvs missing from the flashed table
    assert any(b.startswith("partition table:") for b in c.check(tmp_path)[0])

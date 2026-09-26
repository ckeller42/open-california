"""The NimBLE bond store on the kv-store (``firmware/components/cali_ble_nimble/ble_store_kv.c``, #154).

Drives the store callbacks ``cali_ble_store_init()`` installs in ``ble_hs_cfg`` through
``store-cli`` (``firmware/components/cali_ble_nimble/test/store_cli.c``), linked against the real
NimBLE host objects of the host build — so it needs the same 32-bit Linux toolchain as the BLE e2e
tests (``linux_only``; runs in the firmware-host-e2e CI job / ``tools/ci.sh firmware``). Each
``run()`` is a fresh process = a reboot: what survives it was persisted.
"""
import struct
import subprocess
import zlib

import pytest

from .conftest import HOST_DIR

# One xdist worker: every test's fixture runs `make` in firmware/host (tools/ci.sh test uses -n auto).
pytestmark = [pytest.mark.linux_only, pytest.mark.xdist_group("firmware-host-build")]

MAX_BONDS = 3   # firmware/host/syscfg/syscfg.h BLE_STORE_MAX_BONDS
MAX_CCCDS = 8   # ... BLE_STORE_MAX_CCCDS


@pytest.fixture(scope="module")
def store_cli():
    subprocess.run(["bash", str(HOST_DIR / "fetch_nimble.sh")], check=True)
    subprocess.run(["make", "-C", str(HOST_DIR), "store-cli"], check=True)
    return HOST_DIR / "store-cli"


def run(store_cli, store, script):
    """One boot of the store on ``store``: stdout lines (the store's LOG lines first)."""
    r = subprocess.run([str(store_cli), str(store)], input=script, capture_output=True, text=True,
                       timeout=60, check=True)
    return r.stdout.splitlines()


def _record(value: bytes) -> bytes:
    body = struct.pack("<I", len(value)) + value
    return body + struct.pack("<I", zlib.crc32(body))


def test_write_read_by_addr_and_idx_and_full_store(store_cli, tmp_path):
    out = run(store_cli, tmp_path, "".join("wsec peer %x %x\n" % (0x10 + i, 0xA0 + i)
                                           for i in range(MAX_BONDS + 1)))
    assert out == ["rc=0"] * MAX_BONDS + ["rc=27"]            # BLE_HS_ESTORE_CAP when full
    # A rewrite of an existing peer updates in place (no capacity needed).
    assert run(store_cli, tmp_path, "wsec peer 11 b1\nrsec peer 11 0\n") == [
        "rc=0", "rc=0 addr=11 ltk=b1"]
    # Restart: by peer_addr, and by BLE_ADDR_ANY + idx (NimBLE's iteration).
    assert run(store_cli, tmp_path, "rsec peer 12 0\nrsec peer 99 0\n"
                                    "rsec peer any 0\nrsec peer any 1\nrsec peer any 2\n"
                                    "rsec peer any 3\n") == [
        "rc=0 addr=12 ltk=a2", "rc=5",                        # BLE_HS_ENOENT
        "rc=0 addr=10 ltk=a0", "rc=0 addr=11 ltk=b1", "rc=0 addr=12 ltk=a2", "rc=5"]
    # our and peer tables are separate.
    assert run(store_cli, tmp_path, "rsec our 10 0\n") == ["rc=5"]


def test_delete_compacts_and_survives_restart(store_cli, tmp_path):
    run(store_cli, tmp_path, "wsec our 10 a0\nwsec our 11 a1\nwsec our 12 a2\n")
    assert run(store_cli, tmp_path, "dsec our 10 0\ndsec our 10 0\n") == ["rc=0", "rc=5"]
    assert run(store_cli, tmp_path, "rsec our any 0\nrsec our any 1\nrsec our any 2\n") == [
        "rc=0 addr=11 ltk=a1", "rc=0 addr=12 ltk=a2", "rc=5"]
    assert (tmp_path / "sec_our_2.kv").read_bytes() == _record(b"")   # freed slot = empty value
    # The freed slot takes a new bond.
    assert run(store_cli, tmp_path, "wsec our 13 a3\nrsec our 13 0\n") == [
        "rc=0", "rc=0 addr=13 ltk=a3"]


def test_cccd_matching_write_delete_and_restart(store_cli, tmp_path):
    assert run(store_cli, tmp_path, "wcccd 10 5 1\nwcccd 10 7 2\nwcccd 11 5 1\nwcccd 10 5 3\n") == [
        "rc=0"] * 4
    assert run(store_cli, tmp_path, "rcccd 10 5 0\nrcccd 10 0 1\nrcccd any 5 1\nrcccd 12 0 0\n") == [
        "rc=0 addr=10 h=5 f=3",                               # rewrite updated in place
        "rc=0 addr=10 h=7 f=2",                               # handle 0 = any, idx'th match
        "rc=0 addr=11 h=5 f=1",                               # ANY peer, handle 5, 2nd match
        "rc=5"]
    assert run(store_cli, tmp_path, "dcccd 10 5 0\n") == ["rc=0"]
    assert run(store_cli, tmp_path, "rcccd any 0 0\nrcccd any 0 1\nrcccd any 0 2\n") == [
        "rc=0 addr=10 h=7 f=2", "rc=0 addr=11 h=5 f=1", "rc=5"]
    full = "".join("wcccd %x 9 1\n" % (0x20 + i) for i in range(MAX_CCCDS - 2 + 1))
    assert run(store_cli, tmp_path, full)[-1] == "rc=27"


def test_corrupt_and_wrong_size_records_boot_unpaired(store_cli, tmp_path):
    run(store_cli, tmp_path, "wsec peer 10 a0\nwsec peer 11 a1\n")
    f = tmp_path / "sec_peer_0.kv"
    f.write_bytes(f.read_bytes()[:-3])                        # torn record
    (tmp_path / "sec_our_0.kv").write_bytes(_record(b"\x01\x02\x03"))   # valid CRC, wrong size
    out = run(store_cli, tmp_path, "rsec peer 10 0\nrsec peer any 0\nrsec peer any 1\n"
                                   "rsec our any 0\n")
    assert out == ["LOG store: corrupt record sec_our_0 ignored",
                   "LOG store: corrupt record sec_peer_0 ignored",
                   "rc=5", "rc=0 addr=11 ltk=a1", "rc=5", "rc=5"]
    # The next write rewrites the table over the corrupt slot: no log on the following boot.
    run(store_cli, tmp_path, "wsec peer 12 a2\n")
    assert run(store_cli, tmp_path, "rsec peer any 0\nrsec peer any 1\nrsec peer any 2\n") == [
        "LOG store: corrupt record sec_our_0 ignored",        # our table untouched, still corrupt
        "rc=0 addr=11 ltk=a1", "rc=0 addr=12 ltk=a2", "rc=5"]


def test_a_crash_mid_delete_duplicate_loads_once_and_deletes_for_good(store_cli, tmp_path):
    """Delete-compaction rewrites slot by slot: deleting A from [A, B] writes slot0=B, then clears
    slot1. Power loss in between leaves [B, B] on disk; it must load as ONE record, so a later
    delete of B removes it for good (not just the first copy, resurrecting B on the next boot)."""
    run(store_cli, tmp_path, "wsec peer 10 a0\nwsec peer 11 a1\n"
                             "wcccd 10 5 1\nwcccd 11 5 1\nwcccd 11 7 2\n")
    # The crash-shaped state: slot 0 already rewritten with slot 1's record, slot 1 not yet cleared.
    (tmp_path / "sec_peer_0.kv").write_bytes((tmp_path / "sec_peer_1.kv").read_bytes())
    (tmp_path / "cccd_0.kv").write_bytes((tmp_path / "cccd_1.kv").read_bytes())
    out = run(store_cli, tmp_path, "rsec peer any 0\nrsec peer any 1\n"
                                   "rcccd any 0 0\nrcccd any 0 1\nrcccd any 0 2\n")
    assert out == ["LOG store: duplicate record sec_peer_1 ignored",
                   "LOG store: duplicate record cccd_1 ignored",
                   "rc=0 addr=11 ltk=a1", "rc=5",
                   "rc=0 addr=11 h=5 f=1", "rc=0 addr=11 h=7 f=2", "rc=5"]
    # Reads don't persist, so this boot still sees (and logs) the duplicates; the deletes persist.
    assert run(store_cli, tmp_path, "dsec peer 11 0\ndcccd 11 5 0\n") == [
        "LOG store: duplicate record sec_peer_1 ignored",
        "LOG store: duplicate record cccd_1 ignored", "rc=0", "rc=0"]
    assert run(store_cli, tmp_path, "rsec peer 11 0\nrsec peer any 0\n"
                                    "rcccd 11 5 0\nrcccd any 0 0\nrcccd any 0 1\n") == [
        "rc=5", "rc=5", "rc=5", "rc=0 addr=11 h=7 f=2", "rc=5"]   # no resurrection, no log

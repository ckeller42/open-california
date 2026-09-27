"""cali_net on the host: real POSIX sockets on 127.0.0.1 plus a scripted fake WiFi (#154).

``firmware/components/platform/host/net_host.c`` implements ``cali_net.h`` so the WiFi state
machine and the setup flow run in CI without radios. The fake WiFi reads a script (one rule per
line: ``ap <ssid> <rssi> <secure>``, ``join <ssid> ok <ip>`` | ``fail <reason>`` | ``ok-after <n>``,
``drop-after <ms>``); a missing or empty script means "no networks, every join fails NOT_FOUND"
(ruling R2). Events come only out of ``cali_net_host_poll(now_ms)`` — the tick — never from the
call that caused them. Driven through the line-protocol CLI
``firmware/components/platform/test/net_host_cli.c``, compiled with the host ``cc`` (runs on macOS).

.. test:: Host cali_net: scripted fake WiFi events and non-blocking loopback sockets
   :id: T_FW_NET_HOST
   :links: R_FW_WIFI_PROVISION
"""
import shutil
import socket
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
COMPONENTS = ROOT / "firmware" / "components"
PLATFORM = COMPONENTS / "platform"


@pytest.fixture(scope="module")
def net_cli(tmp_path_factory):
    cc = shutil.which("cc") or pytest.skip("no C compiler")
    out = tmp_path_factory.mktemp("net") / "net_host_cli"
    subprocess.run([cc, "-std=c99", "-Wall", "-Wextra", "-Werror",
                    "-I", str(PLATFORM / "include"), "-I", str(COMPONENTS / "cali_core" / "include"),
                    "-I", str(ROOT / "csrc"),
                    str(PLATFORM / "host" / "platform_host.c"), str(PLATFORM / "host" / "net_host.c"),
                    str(PLATFORM / "test" / "net_host_cli.c"), "-o", str(out)], check=True)
    return out


def run(net_cli, script, commands):
    """Run one net_host_cli process with fake-wifi ``script`` and ``commands`` on stdin; stdout."""
    r = subprocess.run([str(net_cli), str(script)], input=commands, capture_output=True, text=True,
                       timeout=30, check=True)
    return r.stdout


def test_fake_wifi_join_and_drop(net_cli, tmp_path):
    script = tmp_path / "wifi.txt"; script.write_text("ap minsel -55 1\nap other -80 1\njoin minsel ok 192.168.1.42\ndrop-after 500\n")
    out = run(net_cli, script, "scan\npoll 1\nsta_start minsel test-psk-1234\npoll 1\npoll 600\n")
    assert out.splitlines() == ["EV SCAN_DONE NONE 0 2", "EV STA_GOT_IP NONE 192.168.1.42 0", "EV STA_LOST NONE 0 0"]


def test_fake_wifi_wrong_password(net_cli, tmp_path):
    script = tmp_path / "wifi.txt"; script.write_text("ap minsel -55 1\njoin minsel fail auth\n")
    out = run(net_cli, script, "sta_start minsel wrong-psk-99\npoll 1\n")
    assert out.splitlines() == ["EV STA_FAILED AUTH 0 0"]


def test_tcp_roundtrip(net_cli, tmp_path):
    # driver command `tcp_echo`: tcp_listen(0) → prints "PORT <n>", then polls accept/recv/send and
    # answers every received line with the same bytes upper-cased, through cali_net's tcp_* only
    p = subprocess.Popen([str(net_cli), str(tmp_path / "none.txt")], stdin=subprocess.PIPE,
                         stdout=subprocess.PIPE, text=True, bufsize=1)
    p.stdin.write("tcp_echo\n"); p.stdin.flush()
    port = int(p.stdout.readline().split()[1])
    with socket.create_connection(("127.0.0.1", port), timeout=5) as s:
        s.sendall(b"ping\n"); assert s.recv(16) == b"PING\n"
    p.stdin.write("quit\n"); p.stdin.flush(); p.wait(5)


def test_udp_roundtrip(net_cli, tmp_path):
    # driver command `udp_echo`: udp_bind(0) → "PORT <n>", then answers ONE datagram upper-cased to
    # its sender via udp_recvfrom/udp_sendto and prints "FROM 127.0.0.1" (the captive DNS's path)
    p = subprocess.Popen([str(net_cli), str(tmp_path / "none.txt")], stdin=subprocess.PIPE,
                         stdout=subprocess.PIPE, text=True, bufsize=1)
    p.stdin.write("udp_echo\n"); p.stdin.flush()
    port = int(p.stdout.readline().split()[1])
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
        s.settimeout(5)
        s.sendto(b"query", ("127.0.0.1", port))
        assert s.recvfrom(64)[0] == b"QUERY"
    assert p.stdout.readline().strip() == "FROM 127.0.0.1"
    p.stdin.write("quit\n"); p.stdin.flush(); p.wait(5)


def test_missing_script_means_no_networks(net_cli, tmp_path):
    # Ruling R2: no script (or an empty one) is not an error — nothing visible, every join NOT_FOUND.
    for script in (tmp_path / "absent.txt", tmp_path / "empty.txt"):
        if script.name == "empty.txt":
            script.write_text("")
        out = run(net_cli, script, "scan\nsta_start minsel test-psk-1234\npoll 1\n")
        assert out.splitlines() == ["EV SCAN_DONE NONE 0 0", "EV STA_FAILED NOT_FOUND 0 0"]


def test_events_only_from_poll(net_cli, tmp_path):
    # An operation never calls the sink itself: nothing appears until the tick polls.
    script = tmp_path / "wifi.txt"; script.write_text("ap minsel -55 1\njoin minsel ok 10.0.0.7\n")
    out = run(net_cli, script, "sta_start minsel test-psk-1234\nmark\npoll 1\n")
    assert out.splitlines() == ["MARK", "EV STA_GOT_IP NONE 10.0.0.7 0"]


def test_ok_after_fails_n_times_with_other_then_joins(net_cli, tmp_path):
    script = tmp_path / "wifi.txt"; script.write_text("ap minsel -55 1\njoin minsel ok-after 2\n")
    cmds = "sta_start minsel test-psk-1234\npoll 1\n" * 3
    lines = run(net_cli, script, cmds).splitlines()
    assert lines[:2] == ["EV STA_FAILED OTHER 0 0"] * 2
    assert lines[2].startswith("EV STA_GOT_IP NONE ") and lines[2] != "EV STA_GOT_IP NONE 0 0"
    assert len(lines) == 3


def test_drop_after_counts_from_got_ip_delivery(net_cli, tmp_path):
    # GOT_IP delivered at t=100; drop-after 500 → LOST due at t=600, not 500 after sta_start.
    script = tmp_path / "wifi.txt"; script.write_text("join minsel ok 192.168.1.42\ndrop-after 500\n")
    out = run(net_cli, script, "sta_start minsel test-psk-1234\npoll 100\npoll 499\nmark\npoll 1\n")
    assert out.splitlines() == ["EV STA_GOT_IP NONE 192.168.1.42 0", "MARK", "EV STA_LOST NONE 0 0"]


def test_sta_stop_reports_lost_only_if_up_and_rssi(net_cli, tmp_path):
    script = tmp_path / "wifi.txt"; script.write_text("ap minsel -55 1\njoin minsel ok 192.168.1.42\n")
    out = run(net_cli, script, "sta_stop\npoll 1\nrssi\nsta_start minsel test-psk-1234\npoll 1\nrssi\n"
                               "sta_stop\npoll 1\nrssi\n")
    assert out.splitlines() == ["RSSI 0", "EV STA_GOT_IP NONE 192.168.1.42 0", "RSSI -55",
                                "EV STA_LOST NONE 0 0", "RSSI 0"]


def test_ap_start_and_stop(net_cli, tmp_path):
    out = run(net_cli, tmp_path / "none.txt", "ap_stop\npoll 1\nap_start\npoll 1\nap_stop\npoll 1\n")
    assert out.splitlines() == ["EV AP_STARTED NONE 0 0", "EV AP_STOPPED NONE 0 0"]


def test_scan_lists_the_visible_networks(net_cli, tmp_path):
    script = tmp_path / "wifi.txt"; script.write_text("# comment\n\nap minsel -55 1\nap open-cafe -70 0\n")
    out = run(net_cli, script, "scan\npoll 1\naps\n")
    assert out.splitlines() == ["EV SCAN_DONE NONE 0 2", "AP minsel -55 1", "AP open-cafe -70 0"]


def test_bad_arguments_are_refused_without_an_event(net_cli, tmp_path):
    # SSID longer than NET_SSID_MAX (32) or a PSK shorter than NET_PSK_MIN (8): rc -1, no event.
    out = run(net_cli, tmp_path / "none.txt", "sta_start %s test-psk-1234\nsta_start minsel short\npoll 1\n"
              % ("x" * 33))
    assert out.splitlines() == ["ERR sta_start -1", "ERR sta_start -1"]


def test_malformed_script_is_refused(net_cli, tmp_path):
    script = tmp_path / "wifi.txt"; script.write_text("join minsel maybe\n")
    r = subprocess.run([str(net_cli), str(script)], input="", capture_output=True, text=True, timeout=30)
    assert r.returncode == 2

"""cali_http: the portable single-connection HTTP/1.1 responder over cali_net sockets.

``firmware/components/cali_core/http_core.c`` accepts one connection at a time, reads until
``\\r\\n\\r\\n`` plus ``Content-Length`` body bytes, dispatches one request to the registered handler,
sends one ``Connection: close`` response and closes. Driven here through
``test/http_fake_sock.c``: a ``cali_net_t`` whose tcp ops replay a script (one fragment per poll,
sends capped at 64 bytes), with a handler answering ``GET /hello`` and ``POST /echo``.

.. test:: HTTP core: fragments, Content-Length bodies, limits, single request per connection
   :id: T_FW_HTTP_CORE
   :links: R_FW_HTTP_STATUS
"""

import shutil
import subprocess
from pathlib import Path

import pytest

from tools.wifi_consts import CONSTS

ROOT = Path(__file__).resolve().parents[2]
CORE = ROOT / "firmware" / "components" / "cali_core"
REQ_MAX = CONSTS["NET_HTTP_REQ_MAX"]


@pytest.fixture(scope="module")
def http_cli(tmp_path_factory):
    cc = shutil.which("cc") or pytest.skip("no C compiler")
    out = tmp_path_factory.mktemp("http") / "http_fake_sock"
    subprocess.run(
        [
            cc,
            "-std=c99",
            "-Wall",
            "-Wextra",
            "-Werror",
            "-I",
            str(CORE / "include"),
            "-I",
            str(ROOT / "csrc"),
            str(CORE / "http_core.c"),
            str(CORE / "test" / "http_fake_sock.c"),
            "-o",
            str(out),
        ],
        check=True,
    )
    return out


def _esc(s):
    return s.replace("\\", "\\\\").replace("\r", "\\r").replace("\n", "\\n").replace("\0", "\\0")


def drive(cli, lines, args=()):
    """Feed script ``lines`` to the driver; returns its raw stdout (sent bytes + markers)."""
    proc = subprocess.run(
        [str(cli), *args],
        input=("\n".join(lines) + "\n").encode(),
        capture_output=True,
        check=True,
        timeout=10,
    )
    assert proc.stderr.decode() == ("init=-1\n" if "nolisten" in args else "init=0\n")
    return proc.stdout.decode("latin-1")


def session(cli, fragments, gap_ms=10, tail=(10, 10), eof=False, setup=(), args=()):
    """Connect, feed one fragment per poll ``gap_ms`` apart, then poll at each ``tail`` offset.

    ``setup`` lines (``sendmax``/``sendblock``/``big``/...) go first; ``args`` go to the driver.

    :returns: ``(sent, closed)`` — the raw bytes the core sent, and whether the core itself closed
        the connection (before the driver's final ``cali_http_stop``).
    """
    lines = list(setup) + ["conn"] + ["frag " + _esc(f) for f in fragments] + (["eof"] if eof else [])
    lines += ["tick %d" % gap_ms for _ in fragments] + ["tick %d" % t for t in tail]
    raw = drive(cli, lines, args)
    if "nolisten" not in args:
        assert raw.startswith("<accept>")
        raw = raw[len("<accept>") :]
    before, _, after = raw.partition("<stopped>")
    assert after in ("", "<closed>")
    closed = before.endswith("<closed>")
    return before[: -len("<closed>")] if closed else before, closed


def run(cli, fragments, **kw):
    sent, closed = session(cli, fragments, **kw)
    assert closed, "core left the connection open"
    return sent


HELLO_200 = "HTTP/1.1 200 OK\r\nContent-Type: text/plain\r\nContent-Length: 2\r\nConnection: close\r\n\r\nhi"


def test_hello_response_byte_exact(http_cli):
    assert run(http_cli, ["GET /hello HTTP/1.1\r\nHost: x\r\n\r\n"]) == HELLO_200


def test_request_in_fragments(http_cli):
    out = run(http_cli, ["GET /hel", "lo HTTP/1.1\r\nHost: x\r\n", "\r\n"])
    assert (
        out.startswith("HTTP/1.1 200 OK\r\n")
        and "Content-Length: 2\r\n" in out
        and out.endswith("\r\n\r\nhi")
    )
    assert "Content-Type: text/plain\r\n" in out and "Connection: close\r\n" in out


def test_terminator_split_across_fragments(http_cli):
    out = run(http_cli, ["GET /hello HTTP/1.1\r\nHost: x\r", "\n\r", "\n"])
    assert out.startswith("HTTP/1.1 200 OK\r\n") and out.endswith("\r\n\r\nhi")


def test_post_body_by_content_length(http_cli):
    out = run(http_cli, ["POST /echo HTTP/1.1\r\nContent-Length: 5\r\n\r\nab", "cde"])
    assert out.endswith("\r\n\r\nabcde")


def test_header_complete_body_not_yet_waits(http_cli):
    sent, closed = session(http_cli, ["POST /echo HTTP/1.1\r\ncontent-length: 3\r\n\r\n"])
    assert sent == "" and not closed


def test_content_length_zero(http_cli):
    out = run(http_cli, ["POST /echo HTTP/1.1\r\nContent-Length: 0\r\n\r\n"])
    assert out.startswith("HTTP/1.1 200 OK\r\n") and out.endswith(
        "Content-Length: 0\r\nConnection: close\r\n\r\n"
    )


def test_unhandled_404(http_cli):
    out = run(http_cli, ["GET /nope?x=1 HTTP/1.1\r\n\r\n"])
    assert out.startswith("HTTP/1.1 404 Not Found\r\n") and "Content-Type: text/plain\r\n" in out
    head, _, body = out.partition("\r\n\r\n")
    assert "Content-Length: %d\r\n" % len(body) in head + "\r\n" and body


def test_oversized_body_413(http_cli):
    out = run(http_cli, ["POST /echo HTTP/1.1\r\nContent-Length: 5000\r\n\r\n"])
    assert out.startswith("HTTP/1.1 413 ")


def test_body_filling_the_buffer_exactly_is_accepted(http_cli):
    head = "POST /echo HTTP/1.1\r\nContent-Length: %04d\r\n\r\n"
    n = REQ_MAX - len(head % 0)
    body = "b" * n
    out = run(http_cli, [head % n, body[:1000], body[1000:]])
    assert out.startswith("HTTP/1.1 200 OK\r\n") and out.endswith("\r\n\r\n" + body)
    out = run(http_cli, [head % (n + 1)])
    assert out.startswith("HTTP/1.1 413 ")


def test_oversized_header_431(http_cli):
    frag = "GET /hello HTTP/1.1\r\n" + "X-Pad: " + "p" * 900 + "\r\n"
    out = run(http_cli, [frag, frag, frag])
    assert out.startswith("HTTP/1.1 431 ")


def test_malformed_400(http_cli):
    out = run(http_cli, ["GARBAGE\r\n\r\n"])
    assert out.startswith("HTTP/1.1 400 ")


def test_duplicate_content_length_400(http_cli):
    out = run(http_cli, ["POST /echo HTTP/1.1\r\nContent-Length: 2\r\nContent-Length: 2\r\n\r\nab"])
    assert out.startswith("HTTP/1.1 400 ")


def test_transfer_encoding_400(http_cli):
    out = run(http_cli, ["POST /echo HTTP/1.1\r\ntransfer-encoding: chunked\r\n\r\n0\r\n\r\n"])
    assert out.startswith("HTTP/1.1 400 ")


@pytest.mark.parametrize(
    "req",
    [
        "GET hello HTTP/1.1\r\n\r\n",
        "GET /hello FTP/1.1\r\n\r\n",
        "GET  /hello HTTP/1.1\r\n\r\n",
        "\r\n\r\n",
        "GET /hel\0lo HTTP/1.1\r\n\r\n",
        "GET /hello HTTP/1.1\nX: y\r\n\r\n",
        "POST /echo HTTP/1.1\r\nContent-Length: 1x\r\n\r\n",
        "POST /echo HTTP/1.1\r\nContent-Length: \r\n\r\n",
    ],
)
def test_malformed_variants_400(http_cli, req):
    assert run(http_cli, [req]).startswith("HTTP/1.1 400 ")


def test_pipelined_second_request_ignored(http_cli):
    out = run(http_cli, ["GET /hello HTTP/1.1\r\n\r\nGET /hello HTTP/1.1\r\n\r\n"])
    assert out.count("HTTP/1.1 ") == 1 and out.endswith("\r\n\r\nhi")


def test_idle_timeout_closes(http_cli):
    sent, closed = session(http_cli, ["GET /hel"], tail=(5000,))
    assert sent == "" and not closed  # exactly 5 000 ms idle: still open
    sent, closed = session(http_cli, ["GET /hel"], tail=(5001,))
    assert sent == "" and closed


def test_peer_close_mid_request_closes_silently(http_cli):
    sent, closed = session(http_cli, ["GET /hel"], eof=True)
    assert sent == "" and closed


def test_peer_half_close_after_full_request_still_answered(http_cli):
    out = run(http_cli, ["GET /hello HTTP/1.1\r\n\r\n"], eof=True)
    assert out.endswith("\r\n\r\nhi")


def _big(n):
    return "".join(chr(ord("a") + i % 26) for i in range(n))


def test_response_sent_across_polls(http_cli):
    n = 16000
    sent, closed = session(
        http_cli, ["GET /big HTTP/1.1\r\n\r\n"], tail=[10] * 3, setup=["big %d" % n, "sendmax 1000"]
    )
    assert not closed and 0 < len(sent) < n  # 1000 bytes per poll: still sending
    out = run(http_cli, ["GET /big HTTP/1.1\r\n\r\n"], tail=[10] * 40, setup=["big %d" % n, "sendmax 1000"])
    head, _, body = out.partition("\r\n\r\n")
    assert out.count("HTTP/1.1 ") == 1 and "Content-Length: %d" % n in head and body == _big(n)


def test_send_would_block_then_resumes(http_cli):
    out = run(http_cli, ["GET /hello HTTP/1.1\r\n\r\n"], tail=[10] * 5, setup=["sendblock 3"])
    assert out.startswith("HTTP/1.1 200 OK\r\n") and out.endswith("\r\n\r\nhi")


def test_send_stall_times_out(http_cli):
    sent, closed = session(http_cli, ["GET /hello HTTP/1.1\r\n\r\n"], tail=(5000,), setup=["sendblock -1"])
    assert sent == "" and not closed  # exactly 5 000 ms without send progress: still open
    sent, closed = session(http_cli, ["GET /hello HTTP/1.1\r\n\r\n"], tail=(5001,), setup=["sendblock -1"])
    assert sent == "" and closed
    # stalls after a partial send: 1000 bytes in the first poll, then blocked; idle counts from that progress
    lines = [
        "big 16000",
        "sendmax 1000",
        "conn",
        "frag GET /big HTTP/1.1\\r\\n\\r\\n",
        "tick 10",
        "sendblock -1",
        "tick 5000",
        "tick 1",
    ]
    raw = drive(http_cli, lines)
    assert raw.startswith("<accept>HTTP/1.1 200 OK\r\n") and raw.endswith("<closed><stopped>")
    assert len(raw) - len("<accept><closed><stopped>") == 1000  # partial output only


def test_listen_failure_reported_and_poll_inert(http_cli):
    sent, closed = session(http_cli, ["GET /hello HTTP/1.1\r\n\r\n"], args=["nolisten"])
    assert sent == "" and not closed


def test_recv_zero_neither_hangs_nor_drops(http_cli):
    raw = drive(
        http_cli,
        [
            "conn",
            "frag GET /hel",
            "tick 10",
            "recvzero",
            "tick 10",
            "frag lo HTTP/1.1\\r\\n\\r\\n",
            "tick 10",
            "tick 10",
        ],
    )
    assert raw == "<accept>" + HELLO_200 + "<closed><stopped>"


def test_send_zero_is_retried(http_cli):
    out = run(http_cli, ["GET /hello HTTP/1.1\r\n\r\n"], setup=["sendzero"], tail=[10] * 3)
    assert out == HELLO_200


def test_second_connection_after_close(http_cli):
    raw = drive(
        http_cli,
        ["big 3000", "sendmax 1000", "conn", "frag GET /big HTTP/1.1\\r\\n\\r\\n"]
        + ["tick 10"] * 5
        + ["conn", "frag GET /hello HTTP/1.1\\r\\n\\r\\n", "tick 10", "tick 10"],
    )
    first, second = raw.split("<closed>")[:2]
    assert first.startswith("<accept>HTTP/1.1 200 OK\r\n") and first.endswith("\r\n\r\n" + _big(3000))
    assert second == "<accept>" + HELLO_200


def test_no_accept_while_connection_open(http_cli):
    raw = drive(
        http_cli,
        [
            "conn",
            "frag GET /hel",
            "tick 10",
            "conn",
            "tick 10",
            "tick 10",
            "frag lo HTTP/1.1\\r\\n\\r\\n",
            "tick 10",
            "frag GET /hello HTTP/1.1\\r\\n\\r\\n",
            "tick 10",
        ],
    )
    assert raw == "<accept>" + HELLO_200 + "<closed><accept>" + HELLO_200 + "<closed><stopped>"


def test_redirect_carries_location(http_cli):
    out = run(http_cli, ["GET /redir HTTP/1.1\r\n\r\n"])
    assert out == (
        "HTTP/1.1 302 Found\r\nContent-Type: text/plain\r\nContent-Length: 0\r\n"
        "Location: /\r\nConnection: close\r\n\r\n"
    )


def test_redirect_without_location_is_500(http_cli):
    out = run(http_cli, ["GET /redir-bare HTTP/1.1\r\n\r\n"])
    assert out.startswith("HTTP/1.1 500 ") and "Location" not in out


def test_header_overflow_falls_back_to_500(http_cli):
    out = run(http_cli, ["GET /longtype HTTP/1.1\r\n\r\n"])
    assert out.startswith("HTTP/1.1 500 Internal Server Error\r\nContent-Type: text/plain\r\n")
    assert "ttttt" not in out


def test_content_encoding_adds_encoding_and_no_cache(http_cli):
    out = run(http_cli, ["GET /gz HTTP/1.1\r\nHost: x\r\n\r\n"])
    assert out == (
        "HTTP/1.1 200 OK\r\nContent-Type: text/plain\r\nContent-Length: 2\r\n"
        "Content-Encoding: gzip\r\nCache-Control: no-cache\r\nConnection: close\r\n\r\nhi"
    )

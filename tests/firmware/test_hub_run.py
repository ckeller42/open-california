"""The hub client's owner tick (``hub_run.c``) over a fake port (``hub_run_cli``).

The fake port prints ``START``/``STOP``, takes one request frame per ``req``/``replay`` line and opens
every posted frame with the app's context (same key, dir 1) to print ``POST state|res <plaintext>``;
the router is a fake that answers GET /api/state with ``{"st":<n>}``.
"""

import json
import subprocess

import pytest

from .conftest import build_host

pytestmark = [pytest.mark.linux_only, pytest.mark.xdist_group("firmware-host-build")]

CMD = {"id": 1, "m": "POST", "p": "/api/command", "b": "{}", "t": 1_700_000_000_000}  # the fake SNTP clock


def _run(*lines):
    """One hub_run_cli process; returns its output lines."""
    return subprocess.run(
        [str(build_host("hub_run_cli"))],
        input="\n".join(lines) + "\n",
        capture_output=True,
        text=True,
        check=True,
    ).stdout.splitlines()


def _posts(out):
    return [ln for ln in out if ln.startswith("POST ")]


UP = ["cfg", "changed", "online 1", "tick 1000"]


def test_one_request_gives_one_answer_and_one_state():
    out = _run(*UP, "mark", "req " + json.dumps(CMD), "tick 1100")
    assert out[0] == "START" and _posts(out[: out.index("MARK")]) == ['POST state {"st":1}']  # on connect
    after = _posts(out[out.index("MARK") :])
    assert len(after) == 2 and after[0].startswith("POST res ") and after[1] == 'POST state {"st":2}'
    assert json.loads(after[0][9:]) == {"id": 1, "s": 200, "b": '{"ok":true}'}


def test_offline_posts_nothing():
    out = _run("cfg", "changed", "online 0", "tick 1000", "req " + json.dumps(CMD), "tick 1100", "tick 40000")
    assert out[0] == "START" and _posts(out) == []


def test_replayed_frame_is_dropped():
    out = _run(*UP, "req " + json.dumps(CMD), "tick 1100", "mark", "replay", "tick 1200")
    assert _posts(out[out.index("MARK") :]) == []
    assert any(ln.startswith("LOG hub: request rejected") for ln in out)


def test_a_retained_request_after_a_reboot_is_dropped():
    """Coverage row 32 (bench gate): after a reboot the replay ring is empty and SNTP is synced, so a retained
    request (valid tag, fresh t) the broker delivers at the subscribe would run again. It is dropped, not executed;
    the same frame delivered live runs."""
    out = _run(*UP, "mark", "retained " + json.dumps(CMD), "tick 1100")  # a fresh process = a fresh boot
    assert _posts(out[out.index("MARK") :]) == []
    assert "LOG hub: retained request dropped" in out
    out = _run(*UP, "mark", "req " + json.dumps(CMD), "tick 1100")
    assert [ln[:9] for ln in _posts(out[out.index("MARK") :])] == ["POST res ", "POST stat"]


def test_state_every_30_s_and_not_while_http_sends():
    out = _run(*UP, "mark", "tick 20000", "tick 30999", "busy 1", "tick 31000", "busy 0", "tick 31100")
    assert _posts(out[out.index("MARK") :]) == ['POST state {"st":2}']
    assert out[-1] == 'POST state {"st":2}'  # held back while the HTTP core sent, then at once


def test_no_config_nothing_runs():
    assert _run("online 1", "tick 1000", "tick 60000", "status") == ["STATUS 0 0 0 0"]


def test_key_failure_retries_every_30_s_and_reports_it():
    out = _run(
        "keyfail 1",
        "cfg",
        "changed",
        "tick 1000",
        "status",
        "keyfail 0",
        "tick 20000",
        "status",
        "tick 31000",
        "status",
    )
    assert "START" not in out[: out.index("STATUS 0 0 1 0")]
    assert [ln for ln in out if ln.startswith("STATUS")] == [
        "STATUS 0 0 1 0",
        "STATUS 0 0 1 0",
        "STATUS 0 0 0 0",
    ]
    assert out.index("START") > out.index("STATUS 0 0 1 0")


def test_a_new_key_republishes_under_it():
    out = _run(*UP, "mark", "newkey", "tick 1100")
    assert _posts(out[out.index("MARK") :]) == ['POST state {"st":2}']  # the fake app took the new key
    # a LoRa command held under the old key does not run (cali_lora_cmds_drop)
    assert "DROP new data key" in out


def test_the_status_says_why_it_is_not_connected():
    """Ruling R45: the port's reason while the client runs offline; none once connected or without a
    running client."""
    out = _run(
        "why 3",
        "status",
        "cfg",
        "changed",
        "online 0",
        "tick 1000",
        "status",
        "online 1",
        "tick 1100",
        "status",
    )
    assert [ln for ln in out if ln.startswith("STATUS")] == [
        "STATUS 0 0 0 0",
        "STATUS 0 0 0 3",
        "STATUS 1 1 0 0",
    ]


def test_a_new_key_before_the_first_tick_makes_one():
    """The Hub card's New data key may come before the client's first tick: the key is made first."""
    out = _run("cfg", "newkey", "mark")
    assert "LOG hub: new data key" in out

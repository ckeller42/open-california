"""calictl.log — stdout logging with a level knob that still shows up in pytest's capsys.

.. test:: Daemon logging goes to the current stdout with level + name, honours CALICTL_LOG_LEVEL
   :id: T_DAEMON_LOGGING
   :links: R_DAEMON_LOGGING
"""
import logging

from calictl import log


def test_lines_carry_level_and_logger_name(capsys, monkeypatch):
    monkeypatch.delenv("CALICTL_LOG_LEVEL", raising=False)
    monkeypatch.delenv("JOURNAL_STREAM", raising=False)
    monkeypatch.setenv("CALICTL_LOG_TIMESTAMP", "0")
    log.configure(force=True)
    lg = log.get("calictl.serve")
    lg.info("polled %d functions", 14)
    lg.warning("read_all: %s failed after retries: %r", "cooler", ValueError("x"))
    out = capsys.readouterr().out.splitlines()
    assert out[0] == "INFO calictl.serve: polled 14 functions"
    assert out[1].startswith("WARNING calictl.serve: read_all: cooler failed after retries")


def test_level_knob_hides_info_but_not_warning(capsys, monkeypatch):
    monkeypatch.setenv("CALICTL_LOG_LEVEL", "WARNING")
    monkeypatch.setenv("CALICTL_LOG_TIMESTAMP", "0")
    log.configure(force=True)
    lg = log.get("device")
    lg.info("quiet")
    lg.warning("loud")
    assert capsys.readouterr().out == "WARNING calictl.device: loud\n"
    log.configure(level="INFO", force=True)          # leave the process at the default again


def test_timestamp_on_by_default_off_under_journald(capsys, monkeypatch):
    monkeypatch.delenv("CALICTL_LOG_TIMESTAMP", raising=False)
    monkeypatch.delenv("JOURNAL_STREAM", raising=False)
    log.configure(force=True)
    log.get("web").info("hello")
    line = capsys.readouterr().out.strip()
    assert line.endswith("INFO calictl.web: hello") and line[4] == "-" and line[10] == " "
    monkeypatch.setenv("JOURNAL_STREAM", "8:12345")
    log.configure(force=True)
    log.get("web").info("hello")
    assert capsys.readouterr().out == "INFO calictl.web: hello\n"
    monkeypatch.delenv("JOURNAL_STREAM", raising=False)
    log.configure(force=True)


def test_get_normalises_names_under_the_calictl_root():
    assert log.get("calictl.serve").name == "calictl.serve"
    assert log.get("serve").name == "calictl.serve"
    assert log.get("tools.mock_unit").name == "calictl.mock_unit"
    assert isinstance(log.get("x"), logging.Logger)

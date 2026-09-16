"""Daemon logging — stdlib ``logging`` to stdout, one line per event, level from the environment.

Every runtime module logs through ``log = calictl.log.get(__name__)``. Lines look like::

    2026-09-16 16:20:01 INFO calictl.serve: polled 14 functions
    2026-09-16 16:20:31 WARNING calictl.device: read_all: cooler failed after retries: BleakError(...)

Knobs (environment, read once at first use):

* ``CALICTL_LOG_LEVEL`` — ``DEBUG`` / ``INFO`` (default) / ``WARNING`` / ``ERROR``. ``DEBUG`` adds the
  per-poll chatter (``polled …``) and per-command details.
* ``CALICTL_LOG_TIMESTAMP`` — ``1`` (default when stdout is a terminal or a pipe) / ``0``. Under
  systemd the journal already stamps every line, so the timestamp is dropped automatically when
  ``JOURNAL_STREAM`` is set (systemd exports it for services whose stdout is the journal).

Design: the handler writes to *whatever* ``sys.stdout`` is at emit time, not the stream that
existed when the logger was created — so pytest's ``capsys`` sees daemon output exactly as
before the migration from ``print`` (the tests that assert on log text keep working), and a
daemon started by systemd keeps writing to the journal. The CLI's user-facing output
(``calictl status``, ``set``, errors on stderr) stays ``print`` — it is data, not a log.

.. req:: Structured daemon logging with a level knob
   :id: R_DAEMON_LOGGING
   :status: implemented
   :tags: ops, logging

   ``calictl`` runtime modules shall log through the standard ``logging`` module to stdout
   with level and logger name, honour ``CALICTL_LOG_LEVEL``, stamp lines with the local time
   unless running under the systemd journal, and never let a logging failure interrupt the
   daemon.
"""
from __future__ import annotations

import logging
import os
import sys

_CONFIGURED = False
ROOT_NAME = "calictl"


class _StdoutHandler(logging.Handler):
    """Writes to the CURRENT ``sys.stdout`` (pytest swaps it per test; systemd owns it)."""

    def emit(self, record: logging.LogRecord) -> None:
        try:
            msg = self.format(record)
            stream = sys.stdout
            stream.write(msg + "\n")
            stream.flush()
        except Exception:  # noqa: BLE001 — logging must never take the daemon down
            pass


def _want_timestamp() -> bool:
    v = os.environ.get("CALICTL_LOG_TIMESTAMP")
    if v is not None:
        return v not in ("", "0", "false", "no")
    return not os.environ.get("JOURNAL_STREAM")


def configure(level: str | None = None, force: bool = False) -> logging.Logger:
    """Attach the stdout handler to the ``calictl`` logger once; return that logger.

    :param level: overrides ``CALICTL_LOG_LEVEL`` (tests / CLI flags).
    :param force: re-apply the configuration (tests toggling env between cases).
    """
    global _CONFIGURED
    root = logging.getLogger(ROOT_NAME)
    if _CONFIGURED and not force:
        return root
    for h in list(root.handlers):
        if isinstance(h, _StdoutHandler):
            root.removeHandler(h)
    fmt = "%(asctime)s %(levelname)s %(name)s: %(message)s" if _want_timestamp() \
        else "%(levelname)s %(name)s: %(message)s"
    h = _StdoutHandler()
    h.setFormatter(logging.Formatter(fmt, datefmt="%Y-%m-%d %H:%M:%S"))
    root.addHandler(h)
    root.propagate = False
    lvl = (level or os.environ.get("CALICTL_LOG_LEVEL") or "INFO").upper()
    root.setLevel(getattr(logging, lvl, logging.INFO))
    _CONFIGURED = True
    return root


def get(name: str) -> logging.Logger:
    """Logger for a module: ``get(__name__)`` → ``calictl.<module>`` under the configured root."""
    configure()
    if not name.startswith(ROOT_NAME):
        name = ROOT_NAME + "." + name.rsplit(".", 1)[-1]
    return logging.getLogger(name)

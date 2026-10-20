import asyncio
import sys

from calictl import cli, serve


def test_no_ble_never_polls_or_supervises(monkeypatch):
    """serve --no-ble keeps BLE closed

    .. test:: serve --no-ble keeps BLE closed
       :id: T_SERVE_NO_BLE
       :links: R_SERVE_NO_BLE
    """
    srv = serve.Server(None, influx_enabled=False, ble_enabled=False)

    async def boom(*a, **k):
        raise AssertionError("BLE touched")

    monkeypatch.setattr(srv, "poll", boom)
    monkeypatch.setattr(srv._sessions, "supervise", boom)
    try:
        asyncio.run(asyncio.wait_for(srv._main_loop(), 0.3))
    except TimeoutError:
        pass
    assert "bleak" not in sys.modules


def test_no_ble_meta_reports_satellite():
    srv = serve.Server(None, influx_enabled=False, ble_enabled=False)
    meta = serve.ServeBackend(srv, loop=None).state()["_meta"]
    assert meta["session"] == "satellite"
    assert meta["session_mode"] == "off"


def test_ble_meta_unchanged():
    srv = serve.Server(None, influx_enabled=False)
    assert serve.ServeBackend(srv, loop=None).state()["_meta"]["session"] != "satellite"


def test_cli_flag(monkeypatch):
    seen = {}
    monkeypatch.setattr(serve.Server, "run", lambda self: seen.update(ble=self._ble_enabled))
    cli.main(["serve", "--no-ble"])
    assert seen["ble"] is False
    cli.main(["serve"])
    assert seen["ble"] is True


def test_no_ble_refuses_every_ble_entry_point():
    srv = serve.Server(None, influx_enabled=False, ble_enabled=False)
    srv._read_only = False

    async def go():
        assert await srv.on_command("cooler", "power", "on") is None
        assert (await srv.roof_view("view"))["ok"] is False
        assert (await srv.set_session_mode("connect"))["ok"] is False
        await srv.pairing_command("start", None)
        assert srv._pairing is None  # no runner was created

    asyncio.run(go())

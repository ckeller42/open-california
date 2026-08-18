import pytest


@pytest.fixture(autouse=True)
def _isolate_state_cache(tmp_path, monkeypatch):
    """Point the daemon's last-known-state cache at a per-test temp file, so constructing a
    `serve.Server` in tests never reads or writes the real `~/.cache/calictl/last_state.json`."""
    monkeypatch.setenv("CALICTL_STATE_CACHE", str(tmp_path / "last_state.json"))
    # The water push-wait is a live-device behaviour (wait for the heartbeat-driven 1302 push); the
    # mock delivers state synchronously, so disable the wait in tests to keep them fast + deterministic.
    monkeypatch.setenv("CALICTL_WATER_PUSH_WAIT_S", "0")

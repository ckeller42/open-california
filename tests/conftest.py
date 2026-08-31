import pytest


@pytest.fixture(autouse=True)
def _isolate_state_cache(tmp_path, monkeypatch):
    """Point the daemon's last-known-state cache at a per-test temp file, so constructing a
    `serve.Server` in tests never reads or writes the real `~/.cache/calictl/last_state.json`."""
    monkeypatch.setenv("CALICTL_STATE_CACHE", str(tmp_path / "last_state.json"))
    # The water push-wait is a live-device behaviour (wait for the heartbeat-driven 1302 push); the
    # mock delivers state synchronously, so disable the wait in tests to keep them fast + deterministic.
    monkeypatch.setenv("CALICTL_WATER_PUSH_WAIT_S", "0")


@pytest.fixture(scope="session")
def codec_cli(tmp_path_factory):
    """Host-compile csrc/ once per session and return a batched runner:
    ``codec_cli(lines) -> output_lines`` (one per input line, in order, one
    subprocess per call). Skips cleanly when no C compiler exists — the
    codec-parity CI job is the enforcement point. Shared by the codec and ports
    parity suites."""
    import shutil
    import subprocess
    from pathlib import Path
    cc = shutil.which("cc") or shutil.which("gcc")
    if not cc:
        pytest.skip("no C compiler on this machine (the codec-parity CI job enforces)")
    csrc = Path(__file__).resolve().parent.parent / "csrc"
    srcs = [s for s in (csrc / "codec.c", csrc / "ports.c", csrc / "codec_cli.c")
            if s.is_file()]
    exe = tmp_path_factory.mktemp("codec") / "codec_cli"
    subprocess.run([cc, "-std=c99", "-Wall", "-Wextra", "-Werror", "-O1",
                    *map(str, srcs), "-o", str(exe)], check=True)

    def run(lines):
        proc = subprocess.run([str(exe)], input="\n".join(lines) + "\n",
                              capture_output=True, text=True, timeout=120)
        assert proc.returncode == 0, proc.stderr
        out = proc.stdout.splitlines()
        assert len(out) == len(lines), "line-count mismatch: %d in, %d out" % (
            len(lines), len(out))
        return out

    return run

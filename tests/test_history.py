"""The daemon's JSONL log helpers (the per-poll outcome log): append, read back, never raise."""

from calictl import history


def test_jsonl_round_trip_skips_torn_lines_and_filters_by_ts(tmp_path):
    p = str(tmp_path / "sub" / "outcomes.jsonl")  # 'sub' also proves parents are created
    assert history.append_jsonl(p, {"ts": 1000.0, "outcome": "ok"}) is True
    with open(p, "a") as f:
        f.write('{"ts": 1500.0, "outc')  # a torn write (power cut mid-line)
        f.write("\n")
    assert history.append_jsonl(p, {"ts": 2000.0, "outcome": "asleep"}) is True
    assert [r["ts"] for r in history.load_jsonl(p)] == [1000.0, 2000.0]
    assert [r["ts"] for r in history.load_jsonl(p, since=1500.0)] == [2000.0]


def test_jsonl_missing_file_and_unwritable_path_never_raise(tmp_path):
    assert history.load_jsonl(str(tmp_path / "nope.jsonl")) == []
    blocker = tmp_path / "file"
    blocker.write_text("x")
    assert history.append_jsonl(str(blocker / "child.jsonl"), {"ts": 1}) is False

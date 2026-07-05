import json
from unittest import mock
from california.ingest import load_capture


def test_load_capture_uses_filename_label(tmp_path):
    f = tmp_path / "2026-07-05_light-on_001.pklg"
    f.write_bytes(b"\x00")  # content irrelevant; tshark is mocked
    fake_json = json.dumps([{"_source": {"layers": {
        "frame": {"frame.number": "1", "frame.time_relative": "0.1"},
        "btatt": {"btatt.opcode": "0x52", "btatt.handle": "0x002a",
                  "btatt.uuid16": "fff1", "btatt.value": "01"}}}}])
    completed = mock.Mock(returncode=0, stdout=fake_json, stderr="")
    with mock.patch("california.ingest.shutil.which", return_value="/usr/bin/tshark"), \
         mock.patch("california.ingest.subprocess.run", return_value=completed):
        cap = load_capture(str(f))
    assert cap.action == "light-on"
    assert cap.index == 1
    assert len(cap.events) == 1
    assert cap.events[0].op == "write-cmd"

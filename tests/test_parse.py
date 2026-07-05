import json, pathlib
from california.parse import parse_tshark

FX = pathlib.Path(__file__).parent / "fixtures"

def test_parse_extracts_write_cmd():
    packets = json.loads((FX / "tshark_light_on.json").read_text())
    events = parse_tshark(packets)
    assert len(events) == 1
    e = events[0]
    assert e.op == "write-cmd"          # 0x52 = Write Command
    assert e.handle == 0x2a
    assert e.uuid == "0000fff1-0000-1000-8000-00805f9b34fb"
    assert e.value == bytes.fromhex("01ff")
    assert e.frame == 12

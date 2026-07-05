import json, pathlib
from california.classify import classify_transport

FX = pathlib.Path(__file__).parent / "fixtures"

def test_classify_ble():
    packets = json.loads((FX / "tshark_light_on.json").read_text())
    assert classify_transport(packets) == "ble-gatt"

def test_classify_classic():
    packets = json.loads((FX / "tshark_classic_rfcomm.json").read_text())
    assert classify_transport(packets) == "classic"

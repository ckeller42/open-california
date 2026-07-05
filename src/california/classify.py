from __future__ import annotations

def classify_transport(packets: list[dict]) -> str:
    saw_att = saw_classic = False
    for pkt in packets:
        layers = pkt.get("_source", {}).get("layers", {})
        if "btatt" in layers or "btgatt" in layers:
            saw_att = True
        if "btrfcomm" in layers or "btspp" in layers or "btl2cap" in layers and "btatt" not in layers:
            saw_classic = True
    if saw_att:
        return "ble-gatt"
    if saw_classic:
        return "classic"
    return "unknown"

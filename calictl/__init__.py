"""calictl — read and control the VW California Camper Unit over BLE.

Dictionary-driven: all field layouts come from protocol/dictionary.yaml (the
auto-extracted, live-verified protocol map). Layers:

- protocol : parse the dictionary; MSB-first decode/encode of frames
- semantics : the reverse-engineered transforms (water Level=current/Volume=cap,
              energy ×0.1 V scale, per-function Installed feature gating, ...)
- device   : robust BLE transport (connect-on-demand, adapter-reset recovery)
- cli      : `calictl status | get | set`
- mqtt     : Home Assistant discovery
"""
__all__ = ["protocol", "semantics"]
__version__ = "0.1.0"

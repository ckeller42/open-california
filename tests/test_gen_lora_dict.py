"""The Things Stack formatter for the LoRa telemetry is fresh and indexes functions like CODEC_CHARS.

.. test:: The generated TTN formatter matches the dictionary and the C function index
   :id: T_FW_LORA_FORMATTER_FRESH
   :links: R_FW_LORA
"""

import re

from tools import gen_lora_dict

CHARS_H = gen_lora_dict.ROOT / "csrc" / "codec_chars.h"


def test_formatter_is_fresh():
    assert gen_lora_dict.OUT.read_text(encoding="utf-8") == gen_lora_dict.generate(), (
        "run python3 -m tools.gen_lora_dict"
    )


def test_port3_index_is_the_codec_chars_order():
    chars = re.findall(r'^\s*\{"(\w+)", 0x[0-9a-f]{4}\},$', CHARS_H.read_text(encoding="utf-8"), re.M)
    assert [f["name"] for f in gen_lora_dict.functions()] == chars

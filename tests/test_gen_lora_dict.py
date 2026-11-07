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


def test_decode_js_fresh(tmp_path, monkeypatch):
    """The web UI's decoder (lora_decode.js) is fresh too, and ``--check`` fails when it is stale."""
    assert gen_lora_dict.WEB_OUT.read_text(encoding="utf-8") == gen_lora_dict.generate_web(), (
        "run python3 -m tools.gen_lora_dict"
    )
    stale = tmp_path / "lora_decode.js"
    stale.write_text("// stale\n", encoding="utf-8")
    monkeypatch.setattr(gen_lora_dict, "WEB_OUT", stale)
    monkeypatch.setattr("sys.argv", ["gen_lora_dict", "--check"])
    assert gen_lora_dict.main() == 1


def test_both_files_share_one_decoder_body():
    assert (
        gen_lora_dict.body() in gen_lora_dict.generate()
        and gen_lora_dict.body() in gen_lora_dict.generate_web()
    )

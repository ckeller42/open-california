"""The finding-artifact-sync hook watches calictl/pairing.py + calictl/device.py too (#154).

A pairing-SM (enums/timeouts/names) or heartbeat/aux-char constant edit feeds
``tools/gen_c_dict``'s generated ``csrc/codec_chars.h`` / ``csrc/pairing_consts.h`` exactly the
way a ``protocol/dictionary.yaml``/``calictl/overrides.py`` edit feeds ``csrc/codec_dict.h`` (see
commit 4417aa8, #159) — this test exercises the hook script itself (invoked exactly as Claude Code
would, via a PostToolUse stdin event) to pin that watched-path extension, mirroring the
freshness-assertion style of ``tests/test_gen_c_dict.py``.

.. test:: pairing/device edits trigger the codec-regen reminder
   :id: T_FINDING_SYNC_CODEC_ONLY
   :links: R_CHARS_PAIRING_SINGLE_SOURCE
"""

import json
import subprocess
import sys
from pathlib import Path

HOOK = Path(__file__).resolve().parents[1] / "tools" / "hooks" / "finding-artifact-sync.py"


def _run(file_path: str) -> str:
    event = json.dumps({"tool_input": {"file_path": file_path}})
    proc = subprocess.run(
        [sys.executable, str(HOOK)], input=event, capture_output=True, text=True, check=True
    )
    return proc.stdout.strip()


def _context(out: str) -> str:
    return json.loads(out)["hookSpecificOutput"]["additionalContext"]


def test_pairing_edit_flags_the_gen_c_dict_regen():
    msg = _context(_run("calictl/pairing.py"))
    assert "gen_c_dict" in msg
    assert "codec_chars.h" in msg and "pairing_consts.h" in msg
    # the codec-only reminder is narrower than the full protocol-finding checklist
    assert "webui/app.js" not in msg


def test_device_heartbeat_edit_flags_the_gen_c_dict_regen():
    msg = _context(_run("calictl/device.py"))
    assert "gen_c_dict" in msg
    assert "codec_chars.h" in msg and "pairing_consts.h" in msg


def test_dictionary_edit_still_flags_the_full_checklist():
    """Unchanged existing behaviour (#159) — the watched-path extension must not regress it."""
    msg = _context(_run("protocol/dictionary.yaml"))
    assert "gen_codec_vectors" in msg and "gen_c_dict" in msg
    assert "webui/app.js" in msg


def test_unrelated_edit_stays_silent():
    assert _run("README.md") == ""

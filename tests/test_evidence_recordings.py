"""Guard: the APP-RECORDED evidence tier and the committed app recordings stay in step.

Every recording the evidence ledger cites (``tests/vectors/app/<name>.jsonl`` or the bare
``<name>.jsonl`` its APP-RECORDED notes use) must exist, and every committed recording must be
cited — an uncited recording is evidence nobody can find; a cited-but-missing one is a claim
with no proof behind it.
"""

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LEDGER = ROOT / "docs" / "business-logic" / "evidence-ledger.md"
APP = ROOT / "tests" / "vectors" / "app"
# a bare `name.jsonl` or `tests/vectors/app/name.jsonl`, not some other path (e.g. `~/ble.jsonl`)
CITE = re.compile(r"(?<![\w/~.-])(?:tests/vectors/app/)?([\w-]+\.jsonl)")


def test_ledger_cites_every_recording_and_only_existing_ones():
    cited = set(CITE.findall(LEDGER.read_text(encoding="utf-8")))
    on_disk = {p.name for p in APP.glob("*.jsonl")}
    assert not cited - on_disk, (
        "evidence-ledger.md cites recordings missing from tests/vectors/app/: %s" % sorted(cited - on_disk)
    )
    assert not on_disk - cited, (
        "tests/vectors/app/ recordings not cited in docs/business-logic/evidence-ledger.md "
        "(add an APP-RECORDED note): %s" % sorted(on_disk - cited)
    )

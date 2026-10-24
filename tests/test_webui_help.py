"""Guard: every per-screen Help sheet (webui/app.js HELP, #314) links to a docs page that exists."""

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def test_help_docs_links_resolve_to_a_docs_page():
    src = (ROOT / "calictl" / "webui" / "app.js").read_text(encoding="utf-8")
    block = src[src.index("const HELP = {") : src.index("\n};\n", src.index("const HELP = {"))]
    docs = re.findall(r'^  \w+: \["([^"#]+)(?:#[^"]*)?",$', block, re.M)
    assert len(docs) >= 10, docs
    missing = [d for d in docs if not (ROOT / "docs" / d).with_suffix(".md").exists()]
    assert not missing, "HELP links to docs pages that do not exist: %r" % missing

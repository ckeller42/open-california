"""tools.swift_needs: Swift ``- Requirement:`` / ``- Test:`` doc-comment tags -> sphinx-needs import JSON."""

import json
import subprocess
import sys
import textwrap
from pathlib import Path

from tools import swift_needs

KNOWN = {"R_IOS_A", "R_IOS_B", "AD_IOS_X"}


def _tree(tmp_path, **files):
    for name, body in files.items():
        path = tmp_path / "ios" / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(textwrap.dedent(body), encoding="utf-8")
    return tmp_path / "ios"


def test_parsesRequirementAndTestTags(tmp_path):
    ios = _tree(
        tmp_path,
        **{
            "App/Foo.swift": """\
            /// A thing.
            ///
            /// - Requirement: R_IOS_A, AD_IOS_X
            struct Foo {}
            """,
            "AppTests/FooTests.swift": """\
            struct FooTests {
                /// - Test: T_IOS_FOO
                /// - Verifies: R_IOS_A, R_IOS_B
                func foo() {}
            }
            """,
        },
    )
    needs, errors = swift_needs.scan(ios, KNOWN, repo=tmp_path)
    assert errors == []
    impl = needs["I_IOS_FOO_3"]
    assert impl["type"] == "impl"
    assert impl["links"] == ["R_IOS_A", "AD_IOS_X"]
    assert impl["source"] == "https://github.com/ckeller42/open-california/blob/main/ios/App/Foo.swift#L3"
    test = needs["T_IOS_FOO"]
    assert test["type"] == "test"
    assert test["links"] == ["R_IOS_A", "R_IOS_B"]
    assert test["source"].endswith("ios/AppTests/FooTests.swift#L2")
    data = swift_needs.to_json(needs)
    assert data["versions"]["1.0"]["needs"] == needs
    assert data["current_version"] == "1.0"


def test_cliWritesTheRealTree(tmp_path):
    """The real ios/ tree scans clean and every source URL carries the commit."""
    out = tmp_path / "out.json"
    subprocess.run(
        [sys.executable, "-m", "tools.swift_needs", "--out", str(out), "--commit", "abc123"],
        cwd=Path(__file__).resolve().parent.parent,
        check=True,
    )
    needs = json.loads(out.read_text())["versions"]["1.0"]["needs"]
    assert needs
    assert all("/blob/abc123/ios/" in n["source"] for n in needs.values())


def test_unknownRequirementFails(tmp_path):
    ios = _tree(
        tmp_path,
        **{
            "A.swift": "/// - Requirement: R_IOS_A, R_IOS_TYPO\n",
            "ATests.swift": "/// - Test: T_X\n/// - Verifies: R_IOS_NOPE\n",
        },
    )
    _, errors = swift_needs.scan(ios, KNOWN, repo=tmp_path)
    assert any("R_IOS_TYPO" in e for e in errors)
    assert any("R_IOS_NOPE" in e for e in errors)


def test_testWithoutVerifiesFails(tmp_path):
    ios = _tree(tmp_path, **{"T.swift": "/// - Test: T_IOS_LONELY\nfunc t() {}\n"})
    _, errors = swift_needs.scan(ios, KNOWN, repo=tmp_path)
    assert any("T_IOS_LONELY" in e and "Verifies" in e for e in errors)


def test_duplicateTestIdFails(tmp_path):
    body = "/// - Test: T_IOS_DUP\n/// - Verifies: R_IOS_A\n"
    ios = _tree(tmp_path, **{"A.swift": body, "B.swift": body})
    _, errors = swift_needs.scan(ios, KNOWN, repo=tmp_path)
    assert any("T_IOS_DUP" in e and "duplicate" in e for e in errors)

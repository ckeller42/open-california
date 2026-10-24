from tools.docs_touched import verdict


def test_runtime_change_needs_docs_or_a_reason():
    """A runtime change without a docs change fails the gate, unless the PR names why.

    .. test:: A runtime change without docs fails unless the PR names why
       :id: T_DOCS_TOUCHED
       :links: R_DOCS_TOUCHED
    """
    fw = ["firmware/components/cali_core/session.c", "tests/firmware/test_session_fake.py"]
    assert verdict(fw, "") is not None
    assert verdict(fw + ["docs/firmware.md"], "") is None
    assert verdict(["calictl/webui/app.js", "README.md"], "") is None
    assert verdict(["calictl/semantics.py", "protocol/signals.yaml"], "") is None
    assert verdict(["csrc/ports.c"], "Fix.\n\nDocs: n/a — pure refactor, no behaviour change\n") is None
    assert verdict(["csrc/ports.c"], "Docs: n/a") is not None  # a reason is required
    assert verdict(["tests/test_x.py", ".github/workflows/ci.yml"], "") is None  # no runtime code

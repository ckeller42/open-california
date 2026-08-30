"""Sphinx configuration for open-california.

Builds API docs from the code's docstrings (autodoc) and collects
``sphinx-needs`` requirement/test objects declared *in those docstrings*, so
requirements live next to the code they constrain and trace to the tests that
verify them. Runtime deps are mocked so the build needs no BLE/MQTT stack.
"""
import os
import sys

# repo root on the path so autodoc can import calictl.* and tests.*
sys.path.insert(0, os.path.abspath(".."))

project = "open-california"
author = "open-california"
extensions = [
    "sphinx.ext.autodoc",
    "sphinx.ext.napoleon",
    "sphinx_needs",
    "sphinxcontrib.mermaid",   # protocol sequence diagrams (client-side mermaid.js, no Java)
    "myst_parser",             # the markdown docs (business-logic/, protocol.md, ...) on the site
    "sphinx_likec4",           # .. likec4-view:: <id> — iframe embeds of the built /_likec4/ viewer
]
likec4_source_dir = "likec4"

# myst: render ```mermaid fences through sphinxcontrib.mermaid; allow heading anchors for
# the markdown docs' internal links.
myst_fence_as_directive = ["mermaid"]
myst_heading_anchors = 3

# autodoc must not pull the lazily-imported runtime stack (hard rule: stdlib-only import)
autodoc_mock_imports = ["bleak", "paho", "influxdb_client", "yaml", "pytest"]
autodoc_default_options = {"members": True, "undoc-members": False}

# --- sphinx-needs -----------------------------------------------------------
needs_id_required = True
needs_id_regex = r"^[A-Z0-9_]{3,}$"
needs_types = [
    dict(directive="req", title="Requirement", prefix="R_", color="#BFD8D2", style="node"),
    dict(directive="spec", title="Specification", prefix="S_", color="#FFCC00", style="node"),
    dict(directive="impl", title="Implementation", prefix="I_", color="#8CA1BF", style="node"),
    dict(directive="test", title="Test Case", prefix="T_", color="#DCB239", style="node"),
]
# a test that ``:links:`` a requirement satisfies traceability; surface gaps in the build
needs_extra_links = [
    dict(option="verifies", incoming="verified by", outgoing="verifies"),
]
html_theme = "furo"
html_static_path = ["_static"]
html_css_files = ["custom.css"]
html_logo = "_static/logo.png"
html_favicon = "_static/favicon.ico"
# The per-directory README.md files are GitHub-browse navigation, redundant with the site toctrees.
# Main (product-docs) build: the reverse-engineering lab notes are NOT part of this build —
# they are rendered by the separate "evidence" build below onto the same business-logic/ URLs,
# so links keep working while the product docs' nav + search stay free of lab notes.
exclude_patterns = ["_build", "api/openapi.yaml", "README.md", "api/README.md", "protocol/README.md",
                    "business-logic/**"]

# The lab-notebook markdown links freely to code files and repo paths that are not part of the
# rendered site (calictl/*.py, protocol/dictionary.yaml, ...). Those degrade to plain links; do
# not fail the -W build over them. Genuine doc-to-doc links still resolve and are checked.


# The needs_extra_links config is deprecated in sphinx-needs 5 but its replacement
# (needs_links) uses a different schema; keep the working config + silence the one warning.
# - needs.deprecated: needs_extra_links works but its needs_links replacement has a different schema
# - myst.xref_missing / myst.header: the lab-notebook markdown links to code files/repo paths that are
#   not rendered pages (they degrade to plain links) and uses pragmatic heading levels
suppress_warnings = ["needs.deprecated", "myst.xref_missing", "myst.header"]


# ---------------------------------------------------------------------------
# The "evidence" build (sphinx-build -t evidence): renders ONLY the reverse-engineering lab
# notes (docs/business-logic/), visually marked as evidence, published into the same Pages
# artifact at business-logic/ by docs/build_site.sh. Everything else stays out of this build.
if tags.has("evidence"):  # noqa: F821 — `tags` is injected by Sphinx into conf.py
    root_doc = "business-logic/index"
    exclude_patterns = ["_build", "*.rst", "*.md",
                        "api/**", "assets/**", "protocol/**", "screenshots/**"]
    html_title = "open-california — RE lab notes (evidence)"
    html_theme_options = {
        "announcement": ("Reverse-engineering <strong>lab notes — evidence, not product "
                         "documentation</strong>. <a href='https://ckeller42.github.io/open-california/'>"
                         "Back to the docs</a>."),
    }

"""Sphinx configuration for open-california.

Builds API docs from the code's docstrings (autodoc) and collects
``sphinx-needs`` requirement/test objects declared *in those docstrings*, so
requirements live next to the code they constrain and trace to the tests that
verify them. Runtime deps are mocked so the build needs no BLE/MQTT stack.
"""
import os
import sys

# repo root on the path so autodoc can import calictl.* and tests.*
sys.path.insert(0, os.path.abspath("../.."))

project = "open-california"
author = "open-california"
extensions = [
    "sphinx.ext.autodoc",
    "sphinx.ext.napoleon",
    "sphinx_needs",
]

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
html_theme = "alabaster"
html_static_path = ["_static"]
html_logo = "_static/logo.png"
html_favicon = "_static/favicon.ico"
exclude_patterns = ["_build"]

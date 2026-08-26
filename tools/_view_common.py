"""Shared helpers for the protocol-doc-view generators (``tools/gen_*``) — issue #107.

The four generators used to copy-paste the char-UUID expansion, the placed-field predicate,
and the YAML-load boilerplate. One home now; the UUID comes straight from
:func:`calictl.protocol._char_uuid` so the runtime codec stays the single source of truth
(``calictl`` is stdlib-only at import, so tools may import it freely).
"""
from __future__ import annotations

from pathlib import Path

from calictl.protocol import _char_uuid

ROOT = Path(__file__).resolve().parent.parent
DICT_FILE = ROOT / "protocol" / "dictionary.yaml"
SIGNALS_FILE = ROOT / "protocol" / "signals.yaml"


def char_uuid(short) -> str:
    """A BLE characteristic short id (e.g. ``1100``) -> the full 128-bit vendor UUID."""
    return _char_uuid(str(short))


def is_placed(field) -> bool:
    """Only an int offset + int width can be positioned in a frame. ``MERGED_AMBIGUOUS``
    offsets (and ``UNKNOWN`` widths, which only ever appear alongside them) can't be."""
    return isinstance(field.get("offset"), int) and isinstance(field.get("width"), int)


def load_functions(dict_path=None) -> dict:
    """The ``functions:`` map of ``protocol/dictionary.yaml`` (PyYAML — tools-only dep)."""
    import yaml
    doc = yaml.safe_load(Path(dict_path or DICT_FILE).read_text())
    return doc.get("functions", doc)


def load_catalog(signals_path=None) -> dict:
    """The signal catalog (``protocol/signals.yaml``)."""
    import yaml
    return yaml.safe_load(Path(signals_path or SIGNALS_FILE).read_text()) or {}

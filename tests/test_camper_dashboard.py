"""calictl/deploy/camper-dashboard.json: the Grafana import file stays valid (no live Grafana here).

It is pushed by hand (``push_dashboard.py``), so a broken edit only shows on the next push: pin the
import format, unique ids, a non-overlapping grid, that the ESP row reads only measurements
``esp-ingest`` writes and that the UI usage row reads ``ui_ux``.
"""

import json
import pathlib
import re

from calictl import esp_ingest

DASH = json.loads((pathlib.Path(__file__).parent.parent / "calictl/deploy/camper-dashboard.json").read_text())


def _row(title):
    """The panels under the row whose title contains ``title``, up to the next row."""
    panels = DASH["panels"]
    start = next(i for i, p in enumerate(panels) if p["type"] == "row" and title in p["title"]) + 1
    end = next((i for i in range(start, len(panels)) if panels[i]["type"] == "row"), len(panels))
    return panels[start:end]


def _measurements(p):
    return {m for t in p["targets"] for m in re.findall(r'_measurement == "(\w+)"', t["query"])}


def test_dashboard_import_format_ids_and_grid():
    assert [i["name"] for i in DASH["__inputs"]] == ["DS_INFLUXDB"]
    panels = DASH["panels"]
    assert len({p["id"] for p in panels}) == len(panels)
    cells = set()
    for p in panels:
        g = p["gridPos"]
        assert g["x"] + g["w"] <= 24, p["title"]
        mine = {(x, y) for x in range(g["x"], g["x"] + g["w"]) for y in range(g["y"], g["y"] + g["h"])}
        assert not cells & mine, "overlaps: %s" % p["title"]
        cells |= mine
        if p["type"] == "row":
            continue
        assert p["datasource"] == {"type": "influxdb", "uid": "${DS_INFLUXDB}"}, p["title"]
        assert p["targets"] and all('from(bucket: "buspi")' in t["query"] for t in p["targets"]), p["title"]


def test_esp_row_reads_only_what_esp_ingest_writes():
    """Every panel under the "ESP satellite" row reads an ``esp_*`` measurement of ``esp_ingest.DIAG``.

    .. test:: the ESP satellite dashboard row queries only esp-ingest's diagnostics measurements
       :id: T_ESP_INGEST_DIAG_DASHBOARD
       :links: R_ESP_INGEST_DIAG
    """
    esp = _row("ESP satellite")
    written = {m for m, _ in esp_ingest.DIAG.values() if m.startswith("esp_")}
    assert esp
    for p in esp:
        used = _measurements(p)
        assert used and used <= written, (p["title"], used)


def test_ui_usage_row_reads_ui_ux_by_its_tags():
    """The "UI usage" row reads only ``ui_ux`` (calictl's ``POST /api/ux`` + esp-ingest's ``ux``
    records), one panel per event type, filtering on tags both writers set.

    .. test:: the UI usage dashboard row covers every ui_ux event type from both sources
       :id: T_ESP_INGEST_DIAG_UX_DASHBOARD
       :links: R_ESP_INGEST_DIAG
    """
    ui = _row("UI usage")
    assert ui and all(_measurements(p) == {esp_ingest.DIAG["ux"][0]} for p in ui)
    queries = " ".join(t["query"] for p in ui for t in p["targets"])
    for kind in ("view", "control", "timing", "error"):
        assert 'r.type == "%s"' % kind in queries, kind
    assert "exists r.source" in queries  # buspi's own points carry no source tag

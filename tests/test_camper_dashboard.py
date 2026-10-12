"""calictl/deploy/camper-dashboard.json: the Grafana import file stays valid (no live Grafana here).

It is pushed by hand (``push_dashboard.py``), so a broken edit only shows on the next push: pin the
import format, unique ids, a non-overlapping grid and that the ESP row reads only measurements
``esp-ingest`` writes.
"""

import json
import pathlib
import re

from calictl import esp_ingest

DASH = json.loads((pathlib.Path(__file__).parent.parent / "calictl/deploy/camper-dashboard.json").read_text())


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
    panels = DASH["panels"]
    row = next(i for i, p in enumerate(panels) if p["type"] == "row" and "ESP satellite" in p["title"])
    esp = panels[row + 1 :]
    assert esp and all(p["type"] != "row" for p in esp)
    written = {m for m, _ in esp_ingest.DIAG.values()}
    for p in esp:
        used = {m for t in p["targets"] for m in re.findall(r'_measurement == "(\w+)"', t["query"])}
        assert used and used <= written, (p["title"], used)

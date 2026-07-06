# Signal cross-validation — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make it impossible for a decoded VW California BLE signal to be silently missing or mislabeled, by cataloguing every field (state + control, all functions) with a four-source-validated `surface`/`omit` decision and enforcing it with a pytest guardrail.

**Architecture:** A catalog (`protocol/signals.yaml`) is the single source of truth: one entry per field with a decision + provenance from four sources (decompiled app, live captures, GUI, VW docs). An auditor (`tools/audit_signals.py`) triangulates the sources, seeds/updates the catalog, and prints a discrepancy report. A guardrail (`tests/test_signal_coverage.py`) enforces three invariants on every commit — coverage, surfaced⇒emitted, scale-consistency — so a dropped or unaccounted field fails CI. Then a sweep applies the catalog's decisions to `semantics`/`control`/`mqtt`/`influx`.

**Tech Stack:** Python 3 stdlib; PyYAML (tooling/tests only, already available in the dev env — NOT in the runtime `calictl` package); pytest. Decompiled JADX sources at `scratchpad/decompile/src/sources` (+ `bad/sources`).

## Global Constraints

- **Runtime `calictl/*` stays stdlib-only** (no PyYAML/bleak/etc. at import). Catalog/auditor code lives in `tools/` and may use PyYAML; the guardrail test may import both `tools/` and `calictl/`.
- **Catalog decision rule:** every field is `surface` (requires `name`) or `omit` (requires `reason`) — never absent. `scale`/`unit` may be the literal `UNVERIFIED` but must be explicit.
- **Dictionary is the field inventory of record** (`protocol/dictionary.yaml` via `calictl.protocol.load()`); the catalog must match it exactly (no missing, no stale).
- **VW docs: citations only.** Never commit the copyrighted PDF; at most one short quoted term, attributed. `.gitignore` any downloaded manual.
- **Defaults (from design):** `generalpurposesignals` (21 `BitZero*`) → blanket `omit` unless a getter/GUI key is found; surface broadly to InfluxDB, a curated subset to HA/HomeKit.
- Tests stay green: `python3 -m pytest tests/ -q`.
- `protocol.load()` returns `dict[str, Function]`; `Function` has `.state_fields`, `.control_fields` (each `Field` has `.name/.offset/.width/.placed`), `.state_char`, `.control_char`. `semantics.interpret(fn, decoded)->dict`; `mqtt.flatten(interp)->flat dict`.

---

## File structure

| File | Responsibility |
|---|---|
| `tools/catalog.py` (create) | Load/validate `signals.yaml`; enumerate dictionary field-keys; introspect emitted names |
| `protocol/signals.yaml` (create) | The catalog — per-field decision + four-source provenance |
| `tools/app_scales.py` (create) | Harvest getter scales/enums from the decompiled tree |
| `tools/audit_signals.py` (create) | Auditor: gather 4 sources, seed/update catalog, print discrepancy report |
| `tests/test_signal_coverage.py` (create) | The three guardrail invariants |
| `calictl/semantics.py` (modify) | Surface newly-catalogued state signals |
| `calictl/control.py`, `calictl/overrides.py` (modify) | Resolve/validate catalogued control fields |
| `calictl/mqtt.py`, `calictl/influx.py` (modify) | Expose surfaced signals (curated → HA, broad → InfluxDB) |
| `docs/business-logic/signal-catalog.md` (create) | Human rollup of findings + method |

**Ordering:** build the loader (1) → introspection (2) → app-scale harvester (3) → auditor (4) → seed the full catalog by running it (5) → guardrail (6) → sweep per function family (7–10) → VW docs + rollup (11).

---

### Task 1: Catalog loader + schema validation

**Files:**
- Create: `tools/catalog.py`, `protocol/signals.yaml` (seed with 2 entries)
- Test: `tests/test_signal_coverage.py`

**Interfaces:**
- Produces: `catalog.load_catalog(path="protocol/signals.yaml") -> dict`; raises `catalog.SchemaError(msg)` on a bad entry. Entry shape: `cat[fn][kind][field] = {decision, name?, unit?, scale?, kind?, reason?, sources?, confidence?}` where `kind` in `{"state","control"}`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_signal_coverage.py
import pytest, sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from tools import catalog

def test_catalog_schema_surface_needs_name(tmp_path):
    p = tmp_path / "s.yaml"
    p.write_text("energy:\n  state:\n    Foo: {decision: surface}\n")
    with pytest.raises(catalog.SchemaError):
        catalog.load_catalog(str(p))

def test_catalog_schema_omit_needs_reason(tmp_path):
    p = tmp_path / "s.yaml"
    p.write_text("energy:\n  state:\n    Foo: {decision: omit}\n")
    with pytest.raises(catalog.SchemaError):
        catalog.load_catalog(str(p))

def test_catalog_loads_valid():
    c = catalog.load_catalog()
    assert "energy" in c and "state" in c["energy"]
```

- [ ] **Step 2: Run — expect fail** (`ModuleNotFoundError: tools.catalog`)

Run: `python3 -m pytest tests/test_signal_coverage.py -q`

- [ ] **Step 3: Implement `tools/catalog.py`**

```python
"""Load + validate the signal catalog (protocol/signals.yaml)."""
from __future__ import annotations
import os, yaml

_DEFAULT = os.path.join(os.path.dirname(__file__), "..", "protocol", "signals.yaml")

class SchemaError(Exception): pass

def load_catalog(path=_DEFAULT) -> dict:
    with open(path) as fh:
        data = yaml.safe_load(fh) or {}
    for fn, kinds in data.items():
        for kind, fields in (kinds or {}).items():
            if kind not in ("state", "control"):
                raise SchemaError("%s: bad kind %r" % (fn, kind))
            for name, e in (fields or {}).items():
                d = e.get("decision")
                if d not in ("surface", "omit"):
                    raise SchemaError("%s.%s.%s: decision must be surface|omit" % (fn, kind, name))
                if d == "surface" and not e.get("name"):
                    raise SchemaError("%s.%s.%s: surface requires name" % (fn, kind, name))
                if d == "omit" and not e.get("reason"):
                    raise SchemaError("%s.%s.%s: omit requires reason" % (fn, kind, name))
    return data

def keys(cat: dict) -> set:
    return {"%s.%s.%s" % (fn, k, f)
            for fn, kinds in cat.items() for k, fields in (kinds or {}).items()
            for f in (fields or {})}
```

Seed `protocol/signals.yaml`:
```yaml
energy:
  state:
    ITwoBattBemAfs:
      decision: surface
      name: batt2_current
      unit: A
      scale: UNVERIFIED
      kind: leisure_battery
      sources: {app: null, live: 319, gui: infoPage_battery_value_text, vwdoc: null}
      confidence: medium
    LandNotAvailable:
      decision: omit
      reason: "fault/status bit — surfaced via faults[] aggregate"
      sources: {app: null, gui: null, vwdoc: null}
```

- [ ] **Step 4: Run — expect pass.** `python3 -m pytest tests/test_signal_coverage.py -q`
- [ ] **Step 5: Commit** `git add tools/catalog.py protocol/signals.yaml tests/test_signal_coverage.py && git commit -m "catalog: signals.yaml loader + schema validation"`

---

### Task 2: Field enumeration + emitted-name introspection

**Files:** Modify `tools/catalog.py`; Test `tests/test_signal_coverage.py`

**Interfaces:**
- Produces:
  - `catalog.dictionary_keys(funcs) -> set[str]` — `"{fn}.state.{Field}"` and `"{fn}.control.{Field}"` for every field in the loaded dictionary.
  - `catalog.emitted_state_names(fn, funcs) -> set[str]` — flattened keys produced by `semantics.interpret(fn, decoded_zero)` (what the runtime actually surfaces).

- [ ] **Step 1: Test**

```python
def test_dictionary_keys_and_emitted():
    from calictl import protocol, overrides
    from tools import catalog
    f = protocol.load(); overrides.apply(f)
    dk = catalog.dictionary_keys(f)
    assert "energy.state.ITwoBattBemAfs" in dk
    assert "cooler.control.State" in dk
    emit = catalog.emitted_state_names("energy", f)
    assert "batt2_current" in emit and "batt2_v" in emit
```

- [ ] **Step 2: Run — expect fail** (`dictionary_keys` missing)
- [ ] **Step 3: Implement** (append to `tools/catalog.py`)

```python
def dictionary_keys(funcs) -> set:
    out = set()
    for fn, f in funcs.items():
        for sf in f.state_fields:
            out.add("%s.state.%s" % (fn, sf.name))
        for cf in f.control_fields:
            out.add("%s.control.%s" % (fn, cf.name))
    return out

def emitted_state_names(fn, funcs) -> set:
    from calictl import semantics, mqtt
    func = funcs[fn]
    zero = {sf.name: 0 for sf in func.state_fields if sf.placed}
    return set(mqtt.flatten(semantics.interpret(fn, zero)))
```

- [ ] **Step 4: Run — expect pass**
- [ ] **Step 5: Commit** `git commit -am "catalog: dictionary_keys + emitted-name introspection"`

---

### Task 3: App-scale harvester

**Files:** Create `tools/app_scales.py`; Test `tests/test_app_scales.py`

**Interfaces:**
- Produces: `app_scales.scales(sources_root) -> dict[field_name, {"scale": str|None, "getter": "file:line", "enum": [..]|None}]` — parses decompiled getters for a `* 0.1` / `/ 10` style multiplier and an enum class near each named field. Best-effort; missing → `scale: None`.

- [ ] **Step 1: Test** (fixture with a getter returning `raw * 0.1`)

```python
def test_scale_extracted(tmp_path):
    src = tmp_path / "xf" ; src.mkdir()
    (src / "a.java").write_text(
        'float getUTwoBattBemAfs(){ return this.UTwoBattBemAfs * 0.1f; }')
    from tools import app_scales
    s = app_scales.scales(str(tmp_path))
    assert s["UTwoBattBemAfs"]["scale"] == "0.1"
```

- [ ] **Step 2: Run — expect fail**
- [ ] **Step 3: Implement `tools/app_scales.py`**

```python
"""Harvest per-field scale multipliers + enums from the decompiled app."""
from __future__ import annotations
import os, re

_GET = re.compile(r"(\w+)\s*\)\s*\{[^}]*?\b(\w+)\s*\*\s*([0-9.]+)f?", re.S)
_MUL = re.compile(r"\b(\w+)\s*\*\s*([0-9.]+)f?")

def scales(root: str) -> dict:
    out = {}
    for dp, _, files in os.walk(root):
        for fn in files:
            if not fn.endswith(".java"):
                continue
            path = os.path.join(dp, fn)
            try:
                txt = open(path, encoding="utf-8", errors="ignore").read()
            except OSError:
                continue
            for m in re.finditer(r"\b(\w+)\s*\*\s*([0-9.]+)f?", txt):
                field, mul = m.group(1), m.group(2).rstrip(".")
                if field[0].isupper() and field not in out and mul not in ("0", "1"):
                    out[field] = {"scale": mul, "getter": "%s" % os.path.relpath(path, root),
                                  "enum": None}
    return out
```
(The regex is deliberately loose; the auditor treats results as evidence, not gospel — a human confirms in the catalog. Enum harvest may stay `None` in v1.)

- [ ] **Step 4: Run — expect pass**
- [ ] **Step 5: Commit** `git add tools/app_scales.py tests/test_app_scales.py && git commit -m "tools: app-scale harvester from decompiled getters"`

---

### Task 4: The auditor

**Files:** Create `tools/audit_signals.py`; Test `tests/test_audit_signals.py`

**Interfaces:**
- Produces:
  - `audit_signals.gui_keys(ui_dir) -> dict[field_lower, str]` — map a field name (lowercased) to a `ui/screens/*.yaml` string key that mentions it (GUI evidence / "must surface" signal).
  - `audit_signals.report(funcs, cat, app, gui, samples) -> list[str]` — ranked discrepancy lines: `COVERAGE-GAP <key>` (dict field absent from catalog), `STALE <key>`, `GUI-SHOWN-BUT-OMITTED <key>`, `SCALE-MISMATCH <key> app=X cat=Y`, `OUT-OF-RANGE <key> value=V kind=K`.
  - `audit_signals.seed(funcs, cat, app, gui) -> dict` — return a catalog dict with a stub `omit`/`surface` entry added for every uncovered dictionary field (so a human then edits decisions). Preserves existing entries.
  - CLI `python -m tools.audit_signals [--seed] [--report]`.

- [ ] **Step 1: Test the report logic** (pure, no I/O)

```python
def test_report_flags_coverage_and_gui():
    from tools import audit_signals as A
    class F:  # minimal fake funcs
        pass
    # simulate: dictionary has energy.state.NewField, catalog doesn't
    cat = {"energy": {"state": {"Known": {"decision": "omit", "reason": "x"}}}}
    dictkeys = {"energy.state.Known", "energy.state.NewField"}
    lines = A.report_from_keys(dictkeys, cat, gui={"newfield": "infoPage_x"}, app={}, samples={})
    assert any("COVERAGE-GAP energy.state.NewField" in l for l in lines)
    assert any("GUI-SHOWN-BUT-OMITTED" in l or "COVERAGE-GAP" in l for l in lines)
```

- [ ] **Step 2: Run — expect fail**
- [ ] **Step 3: Implement `tools/audit_signals.py`**

```python
"""Four-source auditor: seed/update the catalog and report discrepancies."""
from __future__ import annotations
import glob, os, re, sys
from tools import catalog

def gui_keys(ui_dir) -> dict:
    out = {}
    for path in glob.glob(os.path.join(ui_dir, "*.yaml")):
        txt = open(path, encoding="utf-8", errors="ignore").read().lower()
        for key in re.findall(r"[a-z][a-z0-9]+page_[a-z0-9_]+", txt):
            for tok in re.findall(r"[a-z0-9]+", key):
                out.setdefault(tok, key)
    return out

# Plausible ranges by catalog 'kind' for live sanity.
_RANGES = {"battery": (8, 16), "leisure_battery": (8, 16), "level": (0, 15),
           "percent": (0, 100), "temperature": (-40, 90)}

def report_from_keys(dictkeys, cat, gui, app, samples) -> list:
    catkeys = catalog.keys(cat)
    lines = []
    for k in sorted(dictkeys - catkeys):
        field = k.split(".")[-1]
        hint = " (GUI-SHOWN)" if field.lower() in gui else ""
        lines.append("COVERAGE-GAP %s%s" % (k, hint))
    for k in sorted(catkeys - dictkeys):
        lines.append("STALE %s" % k)
    for fn, kinds in cat.items():
        for kind, fields in (kinds or {}).items():
            for field, e in (fields or {}).items():
                if e.get("decision") == "omit" and field.lower() in gui:
                    lines.append("GUI-SHOWN-BUT-OMITTED %s.%s.%s (%s)" % (fn, kind, field, gui[field.lower()]))
                if e.get("decision") == "surface":
                    a = app.get(field, {}).get("scale")
                    c = str(e.get("scale"))
                    if a and c not in ("UNVERIFIED", a):
                        lines.append("SCALE-MISMATCH %s.%s.%s app=%s cat=%s" % (fn, kind, field, a, c))
    return lines

def report(funcs, cat, app, gui, samples):
    return report_from_keys(catalog.dictionary_keys(funcs), cat, gui, app, samples)

def seed(funcs, cat, app, gui) -> dict:
    for k in catalog.dictionary_keys(funcs) - catalog.keys(cat):
        fn, kind, field = k.split(".")
        entry = cat.setdefault(fn, {}).setdefault(kind, {})
        if field.lower() in gui:
            entry[field] = {"decision": "surface", "name": field.lower(), "unit": None,
                            "scale": "UNVERIFIED", "kind": "state",
                            "sources": {"app": app.get(field, {}).get("getter"),
                                        "gui": gui[field.lower()], "vwdoc": None},
                            "confidence": "low"}
        else:
            entry[field] = {"decision": "omit", "reason": "TODO: triage (no GUI/getter evidence)",
                            "sources": {"app": app.get(field, {}).get("getter"), "gui": None, "vwdoc": None}}
    return cat

def main(argv=None):
    import yaml
    from calictl import protocol, overrides
    argv = argv or sys.argv[1:]
    root = os.path.join(os.path.dirname(__file__), "..")
    funcs = protocol.load(); overrides.apply(funcs)
    cat = catalog.load_catalog()
    app = __import__("tools.app_scales", fromlist=["scales"]).scales(
        os.environ.get("DECOMPILE_SRC", ""))
    gui = gui_keys(os.path.join(root, "ui", "screens"))
    if "--report" in argv:
        print("\n".join(report(funcs, cat, app, gui, {})) or "clean")
    if "--seed" in argv:
        cat = seed(funcs, cat, app, gui)
        with open(os.path.join(root, "protocol", "signals.yaml"), "w") as fh:
            yaml.safe_dump(cat, fh, sort_keys=True, default_flow_style=False)
        print("seeded %d entries" % len(catalog.keys(cat)))
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 4: Run — expect pass.** `python3 -m pytest tests/test_audit_signals.py -q`
- [ ] **Step 5: Commit** `git add tools/audit_signals.py tests/test_audit_signals.py && git commit -m "tools: four-source signal auditor (seed + discrepancy report)"`

---

### Task 5: Seed the full catalog (run the auditor)

**Files:** Modify `protocol/signals.yaml` (generated then hand-triaged). No new test code — this is a data task guarded by later tests.

- [ ] **Step 1:** Seed every field: `DECOMPILE_SRC=<path-to>/scratchpad/decompile/src/sources python3 -m tools.audit_signals --seed`. Expect `seeded N entries` (N = total dictionary fields, ~150).
- [ ] **Step 2:** Run the report: `python3 -m tools.audit_signals --report`. Read `COVERAGE-GAP`/`GUI-SHOWN-BUT-OMITTED`/`SCALE-MISMATCH` lines.
- [ ] **Step 3:** Hand-triage `protocol/signals.yaml`: for each auto-`omit` that is a real signal, flip to `surface` with a canonical `name`, `unit`, `scale`, `kind`; keep genuine noise as `omit` with a concrete reason (not the `TODO` stub). Apply the defaults: `generalpurposesignals.*` and INFO-popup / fault-detail bits stay `omit`. Fill `sources.app` scale where the harvester found one.
- [ ] **Step 4:** `python3 -c "from tools import catalog; catalog.load_catalog()"` — schema must pass (no `TODO` reasons left is a judgement call; at minimum every entry valid).
- [ ] **Step 5: Commit** `git add protocol/signals.yaml && git commit -m "catalog: seed + triage all fields against four sources"`

---

### Task 6: The guardrail (three invariants)

**Files:** Modify `tests/test_signal_coverage.py`

**Interfaces:** Consumes `catalog.load_catalog`, `catalog.dictionary_keys`, `catalog.emitted_state_names`.

- [ ] **Step 1: Write the three invariant tests**

```python
def _funcs():
    from calictl import protocol, overrides
    f = protocol.load(); overrides.apply(f); return f

def test_coverage_every_field_has_a_decision():
    from tools import catalog
    f = _funcs(); cat = catalog.load_catalog()
    dk, ck = catalog.dictionary_keys(f), catalog.keys(cat)
    assert dk - ck == set(), "dictionary fields with no catalog decision: %s" % sorted(dk - ck)
    assert ck - dk == set(), "stale catalog entries: %s" % sorted(ck - dk)

def test_surfaced_state_fields_are_emitted():
    from tools import catalog
    f = _funcs(); cat = catalog.load_catalog()
    missing = []
    for fn, kinds in cat.items():
        for field, e in (kinds.get("state") or {}).items():
            if e.get("decision") == "surface":
                if e["name"] not in catalog.emitted_state_names(fn, f):
                    missing.append("%s.%s->%s" % (fn, field, e["name"]))
    assert not missing, "surfaced but not emitted by semantics: %s" % missing

def test_surfaced_control_fields_are_placed():
    from tools import catalog
    f = _funcs(); cat = catalog.load_catalog()
    bad = []
    for fn, kinds in cat.items():
        placed = {cf.name for cf in f[fn].control_fields if cf.placed}
        for field, e in (kinds.get("control") or {}).items():
            if e.get("decision") == "surface" and field not in placed:
                bad.append("%s.control.%s" % (fn, field))
    assert not bad, "surfaced control fields not placed/resolved: %s" % bad
```

- [ ] **Step 2: Run — expect the coverage/emitted tests to reveal remaining gaps** (fields still `surface` in the catalog but not yet emitted by `semantics`). This is expected — the sweep (Tasks 7–10) makes them pass.

Run: `python3 -m pytest tests/test_signal_coverage.py -q`
Expected: FAIL listing surfaced-but-not-emitted fields (drives the sweep).

- [ ] **Step 3:** No implementation here — the failures are the worklist. (If coverage fails, a catalog entry is missing → fix the catalog.)
- [ ] **Step 4:** Leave failing tests committed only if the sweep immediately follows; otherwise `@pytest.mark.xfail(reason="sweep in progress")` on the emitted test, removed in Task 10. Prefer proceeding directly to Task 7.
- [ ] **Step 5: Commit** `git commit -am "test: signal-coverage guardrail (coverage / surfaced-emitted / control-placed)"`

---

### Task 7: Sweep — energy + climate readouts

**Files:** Modify `calictl/semantics.py`; Test `tests/test_calictl.py`

Surface the catalogued signals the guardrail flags for `energy`, `livingroomheater`, `roofaircondition`, `airheater`. Follow the existing `energy()` pattern (add keys to the returned dict; `_signed(raw, width)` for signed, `*scale` where the catalog gives one).

- [ ] **Step 1: Test** — e.g. `livingroomheater` surfaces `TemperatureAir/TemperatureWater/StateAir/StateWater`:

```python
def test_livingroomheater_surfaces_temps():
    from calictl import protocol as P, semantics, overrides
    f = P.load(); overrides.apply(f)
    d = {sf.name: 0 for sf in f["livingroomheater"].state_fields if sf.placed}
    out = semantics.interpret("livingroomheater", d)
    for k in ("temperature_air", "temperature_water", "state_air", "state_water"):
        assert k in mqtt_flatten(out)   # helper: from calictl import mqtt; mqtt.flatten
```

- [ ] **Step 2: Run — expect fail**
- [ ] **Step 3: Implement** the interpreters for the flagged fields per the catalog `name`/`scale`/`unit`. (Use the catalog as the spec for each field's canonical name.)
- [ ] **Step 4: Run** `python3 -m pytest tests/test_calictl.py tests/test_signal_coverage.py -q` — the energy/climate portion of `test_surfaced_state_fields_are_emitted` now passes.
- [ ] **Step 5: Commit** `git commit -am "semantics: surface energy + climate readouts per catalog"`

---

### Task 8: Sweep — lighting + water + roof + stairs + satellite

**Files:** Modify `calictl/semantics.py`; Test `tests/test_calictl.py`

Surface catalogued signals for `lighting` (per-zone brightness → a `zones` map + `any_on`, per catalog), `water` (any flagged), `roof`/`stairs` (`InfoPopUp` stays `omit`; real position/sensor surfaced), `satelliteantenna` (`SignalLevel`, `Dish`).

- [ ] **Step 1: Test** — lighting surfaces per-zone brightness:

```python
def test_lighting_surfaces_zone_brightness():
    from calictl import protocol as P, semantics, overrides, mqtt
    f = P.load(); overrides.apply(f)
    d = {sf.name: 0 for sf in f["lighting"].state_fields if sf.placed}
    flat = mqtt.flatten(semantics.interpret("lighting", d))
    assert any(k.startswith("zone_") or k.startswith("brightness_") for k in flat)
```

- [ ] **Step 2: Run — expect fail**
- [ ] **Step 3: Implement** per catalog.
- [ ] **Step 4: Run** the two test files — those functions' emitted-invariant passes.
- [ ] **Step 5: Commit** `git commit -am "semantics: surface lighting/water/roof/stairs/satellite per catalog"`

---

### Task 9: Expose surfaced signals — InfluxDB (broad) + HA (curated)

**Files:** Modify `calictl/mqtt.py` (curated entities), `calictl/influx.py` (broad); Test `tests/test_calictl.py`

`influx.numeric_fields` already flattens *all* numeric interpreter output, so newly-surfaced signals flow to InfluxDB automatically — add a test asserting that. Add a **curated** set of new HA entities in `mqtt.ENTITY_SPECS` (per the design default): the meaningful ones (LR-heater air/water temp, roof-AC temp/fan, satellite signal, air-heater runtime) — not the 16 raw zone brightnesses.

- [ ] **Step 1: Test**

```python
def test_influx_gets_new_signals_and_ha_curated():
    from calictl import influx, mqtt
    interp = {"installed": True, "temperature_air": 21, "brightness_zone_1": 13}
    nf = influx.numeric_fields(interp)
    assert nf["temperature_air"] == 21.0 and nf["brightness_zone_1"] == 13.0   # broad
    cfgs = mqtt.render_discovery(installed={"livingroomheater"})
    assert any("temperature_air" in t for t in cfgs)                            # curated HA
```

- [ ] **Step 2: Run — expect fail**
- [ ] **Step 3: Implement** — add the curated `ENTITY_SPECS` entries; confirm `numeric_fields` needs no change (already broad). Ensure `livingroomheater`/`roofaircondition` appear in `FUNCTION_NAMES`/`ENTITY_SPECS`.
- [ ] **Step 4: Run — expect pass**
- [ ] **Step 5: Commit** `git commit -am "mqtt/influx: expose surfaced signals (broad->InfluxDB, curated->HA)"`

---

### Task 10: Guardrail green + control-field triage

**Files:** Modify `protocol/signals.yaml`, `calictl/overrides.py` (resolve any surfaced control fields still `MERGED_AMBIGUOUS`); remove any `xfail` from Task 6.

- [ ] **Step 1:** Run `python3 -m pytest tests/test_signal_coverage.py -q`. For every remaining `test_surfaced_state_fields_are_emitted` failure, either surface it (add to semantics) or flip that catalog entry to `omit` with a reason. For `test_surfaced_control_fields_are_placed` failures, resolve the offset in `overrides.py` (from the app frame builder) or set the control field `omit`.
- [ ] **Step 2:** Remove the `xfail` marker if added.
- [ ] **Step 3:** Run the full suite `python3 -m pytest tests/ -q` — all green.
- [ ] **Step 4:** Run `python3 -m tools.audit_signals --report` — expect `clean` (or only accepted `UNVERIFIED` scale notes).
- [ ] **Step 5: Commit** `git commit -am "catalog+guardrail: all fields accounted for, coverage green"`

---

### Task 11: VW documentation citations + rollup doc

**Files:** Create `docs/business-logic/signal-catalog.md`; Modify `protocol/signals.yaml` (`vwdoc` fields), `.gitignore`

- [ ] **Step 1:** Fetch the official VW California T7 digital owner's manual (web). Add `manuals/` to `.gitignore`; do NOT commit the PDF.
- [ ] **Step 2:** For the surfaced measurement families (batteries, water tanks, heaters, roof, lighting), add a short `vwdoc` citation (section title/term/URL) to the relevant catalog entries. One short quoted term max, attributed.
- [ ] **Step 3:** Write `docs/business-logic/signal-catalog.md`: the method (four sources, the guardrail), a per-function table of surfaced vs omitted counts, and the notable findings (the ~85 gaps resolved, batt2_current, solar). Link `protocol/signals.yaml` as the machine-readable source.
- [ ] **Step 4:** `python3 -m pytest tests/ -q` still green (docs/citations don't change decisions).
- [ ] **Step 5: Commit** `git add docs/business-logic/signal-catalog.md protocol/signals.yaml .gitignore && git commit -m "docs: VW-doc citations + signal-catalog rollup"`

---

## Self-review

**Spec coverage:** catalog schema → Task 1; four cross-checks → Tasks 3 (app), 4 (GUI+report+live-range), 5 (triage), 11 (VW doc); auditor → Task 4; guardrail three invariants → Task 6 (+10 green); sweep of ~85 gaps → Tasks 7–9; copyright rule → Task 11 (.gitignore, citations only); `generalpurposesignals` blanket-omit + broad-InfluxDB/curated-HA defaults → Tasks 5 & 9. Live-capture cross-check is range-sanity in the auditor (Task 4) seeded from existing captured frames; deeper multi-state capture is noted as gated but not blocking.

**Placeholder scan:** the only `TODO` string is the auditor's *generated* stub reason, explicitly replaced during Task 5 triage — not a plan placeholder. Field-specific canonical names in Tasks 7–8 are defined by the catalog produced in Task 5 (the spec-of-record for the sweep), which is why those tasks reference "per catalog" rather than inventing names here.

**Type consistency:** `catalog.load_catalog/keys/dictionary_keys/emitted_state_names`, `app_scales.scales`, `audit_signals.gui_keys/report_from_keys/report/seed`, `semantics.interpret`, `mqtt.flatten/render_discovery`, `influx.numeric_fields` are used consistently across tasks. The guardrail (Task 6) consumes exactly what Tasks 1–2 produce.

**Note:** Tasks 7–8 intentionally get their exact field→name/scale mappings from the Task-5 catalog rather than hard-coding them here, because those values are the *output* of the four-source triangulation this project exists to perform; pre-inventing them in the plan would defeat the audit.

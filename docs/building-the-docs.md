# Sphinx + sphinx-needs docs

The site is TWO builds from one tree: the **product docs** (no lab notes in nav/search)
and the **evidence build** (`-t evidence`, only `business-logic/`, banner-marked), merged by
`docs/build_site.sh` onto one artifact so every relative link keeps working.

Requirements and their test traceability are authored **as `sphinx-needs`
objects inside code docstrings** — `.. req::` next to the implementation,
`.. test:: … :links: R_*` next to the verifying test — and collected here by
autodoc. This keeps requirements as close to the code as possible and makes a
broken/missing trace a build-time failure.

## Build

```sh
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
# HTML (warnings-as-errors keeps traceability honest):
sh docs/build_site.sh                    # full site: product docs + the evidence build, merged
# (equivalent to the two runs below; PYTHON=... overrides the interpreter)
.venv/bin/python -m sphinx -b html  -W docs docs/_build/html            # product docs only
.venv/bin/python -m sphinx -b html  -W -t evidence docs docs/_build/evidence   # RE lab notes only
# needs.json (machine-readable traceability export):
.venv/bin/python -m sphinx -b needs    docs docs/_build/needs
```

## Conventions

- **Requirement:** `.. req::` with `:id: R_<NAME>` in the docstring of the code
  that implements it (e.g. `calictl.semantics.vehicle` → `R_VEHICLE_1004`).
- **Test:** `.. test::` with `:id: T_<NAME>` and `:links: R_<NAME>` in the test
  function's docstring; the link is the trace.
- Add each new autodoc'd module/function to `api.rst` so its needs are collected.
- `needs_id_required = True` — every need must have an explicit ID.
- Verify a trace resolved: `needs.json` → the req's `links_back` lists its test.

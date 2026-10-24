# Sphinx + sphinx-needs docs

The site is TWO builds from one tree: the **product docs** (no lab notes in nav/search)
and the **evidence build** (`-t evidence`, only `business-logic/`, banner-marked), merged by
`docs/build_site.sh` onto one artifact so every relative link keeps working.

Requirements and their test traceability are authored **as `sphinx-needs`
objects inside code docstrings** — `.. req::` next to the implementation,
`.. test:: … :links: R_*` next to the verifying test — and collected here by
autodoc. This keeps requirements as close to the code as possible; a `:links:` to a
nonexistent {term}`ID <Requirement ID>` is a build-time failure (`-W`), while a requirement with no test builds clean
and simply shows an empty `incoming` — check `needs.json` for that.

## Build

```sh
python3 -m venv .venv
.venv/bin/pip install -r docs/requirements.txt
# HTML (warnings-as-errors keeps traceability honest):
sh docs/build_site.sh                    # full site: product docs + the evidence build, merged
# (equivalent to the two runs below; PYTHON=... overrides the interpreter)
.venv/bin/python -m sphinx -b html  -W docs docs/_build/html            # product docs only
.venv/bin/python -m sphinx -b html  -W -t evidence docs docs/_build/evidence   # RE lab notes only
# needs.json (machine-readable traceability export):
.venv/bin/python -m sphinx -b needs    docs docs/_build/needs
```

## Where the site is built and published

- **On every pull request:** the `docs` job in `.github/workflows/ci.yml` runs
  `sh docs/build_site.sh docs/_build/html` (both builds, `-W`) with `docs/requirements.txt` on
  Python 3.12, so a `sphinx -W` failure (a broken `:links:`, a bad cross-reference, a new page
  missing from a toctree) fails the PR instead of first showing up after merge. It only builds;
  nothing is published from a PR. `tools/ci.sh` does not build the docs — run
  `sh docs/build_site.sh` locally for fast feedback.
- **On `main`:** `.github/workflows/docs.yml` runs on a push to `main` that touches `docs/**`,
  `calictl/**`, `tests/**`, `tools/gen_c_dict.py`, `tools/gen_codec_vectors.py` or the workflow itself (plus a manual `workflow_dispatch`).
- **Deploy:** its `build` job runs `sh docs/build_site.sh docs/_build/html` (both builds, `-W`) with
  `docs/requirements.txt` on Python 3.12, uploads the result as a Pages artifact, and the `deploy`
  job publishes it with `actions/deploy-pages` to <https://ckeller42.github.io/open-california/>.
- **Mermaid** renders in the browser, so `-W` does not catch a broken diagram;
  `tests/test_mermaid_syntax.py` (in the normal pytest run) lints the ```` ```mermaid ```` fences
  instead.
- **Screenshots** in `docs/screenshots/` are not built here: `.github/workflows/screenshots.yml`
  re-renders them over the mock unit on a push to `main` that touches `calictl/webui/**`,
  `calictl/semantics.py`, `tools/ux_gallery.py` or `tools/mock_unit.py`, and commits them to
  the `screenshots` branch (`main` takes changes only through PRs), then starts this workflow;
  `build` lays that branch's `docs/screenshots/` over `main`'s copies, which are only the fallback
  for a local build (locally: `tools/ci.sh screenshots`). The README links the branch's shots, and
  the coverage badge JSON lives on the `badges` branch the same way.

## Glossary

`docs/glossary.md` is the only glossary: a MyST ```` ```{glossary} ```` fence with `:sorted:`.

- **Add a term:** a line with the term at column 0, then the definition indented two spaces
  (≤3 lines, link the page that defines it). Synonyms (German unit labels, short forms) are extra
  term lines directly above the same definition.
- **Link it:** `` {term}`Latch` `` in Markdown, ``:term:`Latch` `` in a docstring (case-insensitive;
  `` {term}`text <Latch>` `` for other link text). `sphinx -W` fails on an unknown term.
- **Where to link:** the first meaningful use of a term on each page, in prose only (not in
  headings, code, tables that would break, or Mermaid). Skip common words where the specific
  meaning does not matter.
- **Lab notes** (`business-logic/`) are the separate evidence build, where `{term}` does not
  resolve. Link the glossary entry by absolute URL there:
  `[latch](https://ckeller42.github.io/open-california/glossary.html#term-Latch)`. The anchor
  is `term-` plus the term with spaces as `-` and accents dropped (`term-Flustermodus`).
- **Index:** every term lands in the general index (`genindex`, linked from the start page).
- `tests/test_glossary.py` fails on a second glossary (a hand-written "Glossary" definition
  list or another `glossary` directive), on duplicate terms, and on an orphan entry: a
  definition that no page links to through any of its synonyms (a `{term}`/`:term:` role or a
  `glossary.html#term-…` URL). Link the new term somewhere, or add it to `ORPHAN_ALLOW` with a
  reason.

## Conventions

- Pages are MyST Markdown. RST appears only in Python docstrings (autodoc renders them) and inside
  ```` ```{eval-rst} ```` blocks: the api page's autodoc lines, and `.. req::` blocks on a page
  such as `firmware.md` (a requirement outside such a block is not collected).
- **Requirement:** `.. req::` with `:id: R_<NAME>` in the docstring of the code
  that implements it (e.g. `calictl.semantics.vehicle` → `R_VEHICLE_1004`).
- **Test:** `.. test::` with `:id: T_<NAME>` and `:links: R_<NAME>` in the test
  function's docstring; the link is the trace.
- Add each new autodoc'd module/function to `docs/api.md` (an `auto*::` line inside one of its
  ```` ```{eval-rst} ```` blocks) so its needs are collected.
- `needs_id_required = True` — every need must have an explicit ID.
- Verify a trace resolved: `needs.json` → the req's `links_back` lists its test.

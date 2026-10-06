---
name: webui-change
description: Use when editing the calictl web UI (calictl/webui/*.js, strings.de.js, index.html) or its Playwright e2e tests (tests/e2e/) — adding a control or card, a label, a translation, or anything the ESP satellite also serves.
---

# Change the web UI

The UI is **un-built JS** served by calictl on buspi **and** compiled into the ESP firmware. No
bundler catches mistakes, so the gates below are the only safety net.

## Gates (all must pass)

| Gate | Command | Why |
|---|---|---|
| Type check, baseline **0 errors** | `tools/ci.sh webcheck` (`tsc --checkJs`) | `node --check` only parses; an undeclared identifier once shipped to buspi |
| Semantics twin | `python3.13 -m tools.gen_semantics_vectors --check` | `webui/semantics.js` must match `calictl/semantics.py` on `tests/vectors/semantics.json`; regenerate after changing either |
| ESP bundle fresh | `python3.13 -m tools.gen_c_dict --check` (regenerate without `--check`) | `firmware/web/app_bundle_gen.h` embeds the UI |
| Screens | `calictl/webui/screens.json` regenerated after a `ui/screens/*.yaml` edit (pre-commit checks it) | |
| e2e | `python3.13 -m pytest tests/e2e -q` | every test fails on an uncaught page error |

## Words: the unit's vocabulary, EN + DE

- Every user-visible label/enum uses the **app's own** term (Sofortheizen, Dauerbetrieb,
  Flüstermodus, "Leselichter Wohnraum" …). Take EN + DE from the app's `.cvr` string tables
  (`decompile-app` skill, "Localized UI strings"; tables on buspi). Copy label strings only, never code.
- Only when the app has no string: write a proposal and list it in the PR body for the owner.
- `t()` keys must exist in `strings.de.js` (`tests/test_i18n_de.py`); `tf()` for `{n}` templates.

## UI behaviour rules

- **Never invent state.** If the daemon reports `null`, show the control disabled with a "not known
  yet" note. Never fill in 00:00, area 1 or brightness 0 — the server then sends a value the unit never had.
- **Send only what the user changed.** Let the daemon fill unit-reported fields (e.g. an edit sends no
  `on`/`off`; only the switch does).
- **Optimistic overlay** for a sent value until the command completes, then show the unit's state.
  A re-render must not reset a field from stale state while a command is in flight.
- **Gate like the app** (greyed + reason) but the server gate in `command_precondition` is the real one.

## e2e pitfalls (each cost a review round)

- `page.wait_for_function("async () => …")` **never waits**: the returned Promise is truthy. Use a
  sync predicate or poll `/api/state` from Python.
- Assert the **unit path** (the daemon's state from the mock's frames), not the optimistic overlay —
  otherwise a broken echo still passes. Prove it: break the mock echo, watch the test fail, restore.
- Drive like a user: one `fill`, no extra `dispatch_event("change")` (it hits a re-rendered input).
- Check what was **sent**: record `/api/command` bodies with `page.on("request", …)`.
- Text locators collide when new labels contain old ones ("Reading lights") — use `exact=True`.
- Run a flaky-looking test 5× before calling it fixed.

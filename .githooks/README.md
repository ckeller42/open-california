# Git hooks (local development)

Enable once per clone:

```sh
git config core.hooksPath .githooks
```

## `pre-commit`

Fails the commit if any of these are true:

1. VW/vendor binaries or extracted assets are staged (`*.apk/*.dex/*.jar/…`, `decompile/`,
   `manuals/`, `ui/assets/svg/`, `strings_resolved*`) — belt-and-braces over `.gitignore`.
2. The real vehicle BLE MAC (`20:81:9A:…`) appears in the diff — use a placeholder / env var.
3. A 17-char VIN appears in the diff (PII).
4. `pytest tests/` or `tools.audit_signals --report` fails.
5. Web UI JS is staged (`calictl/webui/*.js`, `jsconfig.json`) and `tsc --checkJs` or
   `node --check` fails (`tools/ci.sh webcheck`; needs Node — skipped with a note if absent).
   `SKIP_TESTS=1` does not bypass this step.

Bypass tests for a docs-only commit: `SKIP_TESTS=1 git commit …`.

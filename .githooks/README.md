# Retired: use pre-commit

The custom `.githooks/pre-commit` hook was replaced by the [pre-commit](https://pre-commit.com)
framework. Its checks now live in [`.pre-commit-config.yaml`](../.pre-commit-config.yaml):
the fast guards (vendor material / vehicle MAC / VIN, import-clean, doc-offset, web-fresh,
codec vectors + C headers, the web-UI `tsc` check) and the linters run on every commit; the
full pytest suite and the signal audit run on `git push` (pre-push stage).

Switch an existing clone over once:

```sh
git config --unset core.hooksPath   # pre-commit refuses to install while this is set
tools/ci.sh dev                     # pip install -r requirements-dev.txt && pre-commit install
```

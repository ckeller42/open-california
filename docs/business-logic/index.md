# Reverse-engineering lab notes

```{warning}
**These are dated lab notes — evidence, not product documentation.** They record how each
protocol fact was established (captures, decompile citations, corrections, dead ends) and keep
their full history on purpose. For the polished documentation, go back to the
[product docs](https://ckeller42.github.io/open-california/).
```

Start here:

- [control-and-actuation.md](control-and-actuation.md) — the write gate ([1003 heartbeat](https://ckeller42.github.io/open-california/glossary.html#term-Heartbeat)), the
  [full-packet](https://ckeller42.github.io/open-california/glossary.html#term-Full-packet) frame model, per-feature actuation status
- [signals.md](signals.md) — the [signal catalog](https://ckeller42.github.io/open-california/glossary.html#term-Catalog) guardrail + scales (all resolved; §5 lists the former `UNVERIFIED` ones)
- [evidence-ledger.md](evidence-ledger.md) — every claim's verification tier (DEVICE / CAPTURE /
  DECOMPILE)
- [DECISIONS.md](DECISIONS.md) — the dated RE changelog (resolved questions + dead ends, newest
  first)

```{toctree}
:maxdepth: 1
:glob:

*
```

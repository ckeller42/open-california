"""Guard: importing the runtime package must NOT pull in the lazy third-party deps.

`calictl/*` is stdlib-only AT IMPORT — bleak/paho/influxdb_client/yaml are imported inside
functions, never at module top (so tests + the CLI run without them installed). This asserts that
invariant. Shared by the Makefile, the pre-commit hook, and CI so there's ONE source of truth.

Exits non-zero (and prints the leaked modules) if any banned module is imported as a side effect.
"""
import sys

import calictl.automation  # noqa: F401
import calictl.cli
import calictl.control  # noqa: E401,F401
import calictl.device
import calictl.influx
import calictl.mqtt
import calictl.overrides
import calictl.protocol  # noqa: E401,F401
import calictl.semantics
import calictl.serve
import calictl.web  # noqa: F401

BANNED = {"bleak", "paho", "paho.mqtt", "influxdb_client", "yaml"}


def main() -> int:
    leaked = BANNED & set(sys.modules)
    if leaked:
        print("runtime pulled non-stdlib at import: %s" % sorted(leaked))
        return 1
    print("import-clean:", sorted(BANNED), "not imported")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

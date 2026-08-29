#!/bin/sh
# Build the full documentation site: the product docs (main build, no lab notes in nav/search)
# plus the reverse-engineering lab notes as a separate "evidence" build merged onto the same
# business-logic/ paths, so every existing relative link keeps working on site and on GitHub.
#
#   sh docs/build_site.sh [outdir]      # default outdir: docs/_build/site
set -e
PY="${PYTHON:-python3}"
DOCS="$(cd "$(dirname "$0")" && pwd)"
OUT="${1:-$DOCS/_build/site}"
"$PY" -m sphinx -b html -W "$DOCS" "$OUT"
"$PY" -m sphinx -b html -W -t evidence "$DOCS" "$DOCS/_build/evidence"
mkdir -p "$OUT/business-logic"
cp -R "$DOCS/_build/evidence/business-logic/." "$OUT/business-logic/"
# The interactive LikeC4 model (architecture + protocol-sequence dynamic views) at /model/.
# Pinned exactly: an unpinned npx would execute whatever npm serves next (supply chain).
npx -y likec4@1.59.2 build -o "$OUT/model" --base ./ "$DOCS/likec4"
echo "site -> $OUT"

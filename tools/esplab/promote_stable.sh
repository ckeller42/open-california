#!/usr/bin/env bash
# Promote a soaked main commit to the stable OTA channel (R_FW_OTA): move the esp-stable tag, then run
# .github/workflows/esp-stable.yml, which republishes that commit's main CI artifact as the esp-stable
# release. A tag already at the sha is fine (it only dispatches). tools/esplab/promote_stable.sh <sha>
set -euo pipefail
sha=$(git rev-parse --verify "${1:?sha}^{commit}"); REPO=ckeller42/open-california   # full 40 hex: the workflow input
git fetch -q origin main esp-stable 2>/dev/null || git fetch -q origin main
git merge-base --is-ancestor "$sha" origin/main || { echo "$sha is not on origin/main" >&2; exit 1; }
# the artifact must exist before the tag moves (a missing one would leave the tag ahead of the release)
RUN=$(gh run list -R "$REPO" --workflow ci.yml --commit "$sha" --branch main --status success --json databaseId -q '.[0].databaseId')
[ -n "$RUN" ] || { echo "no successful main CI run of $sha" >&2; exit 1; }
[ -n "$(gh api "repos/$REPO/actions/runs/$RUN/artifacts" --jq '.artifacts[] | select(.name=="firmware-esp32s3" and .expired==false) | .id')" ] ||
  { echo "CI run $RUN has no live firmware-esp32s3 artifact (expired?): re-run CI or promote a newer sha" >&2; exit 1; }
echo "esp-stable: $(git rev-parse --short esp-stable 2>/dev/null || echo none) -> $(git rev-parse --short "$sha")"
git tag -f esp-stable "$sha" && git push -f origin esp-stable
gh workflow run esp-stable.yml -R "$REPO" --ref main -f sha="$sha"
sleep 5
echo "publishing: $(gh run list -R "$REPO" --workflow esp-stable.yml --limit 1 --json url -q '.[0].url')"
echo "then https://github.com/$REPO/releases/tag/esp-stable; then ota_deploy.sh stable"

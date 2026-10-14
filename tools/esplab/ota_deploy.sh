#!/usr/bin/env bash
# OTA the ESP satellite to a build once CI's firmware-release has published it (R_FW_OTA).
#   tools/esplab/ota_deploy.sh [stable|main|sha] [esp-url]     e.g. ota_deploy.sh main http://192.168.8.183
# stable = the esp-stable tag's build (channel stable); main / a sha = a merge commit (channel latest).
# Sets the ESP's OTA channel, waits for esp-<channel>'s manifest.json to name the sha, checks until
# the ESP sees it (GitHub's CDN can lag ~1-2 min), installs, then waits for the new fw and the 60 s
# rollback window. USB: flash_ci.sh.
set -euo pipefail
REF="${1:-main}"; ESP="${2:-http://192.168.8.183}"; REPO=ckeller42/open-california
chan=latest
# main = its newest commit CI builds: a [skip ci] commit (the screenshot bot) never gets an esp-latest image
if [ "$REF" = stable ]; then sha=$(gh api "repos/$REPO/git/ref/tags/esp-stable" --jq .object.sha); chan=stable
elif [ "$REF" = main ]; then sha=$(gh api "repos/$REPO/commits?sha=main&per_page=20" \
  --jq '[.[] | select(.commit.message | contains("[skip ci]") | not)][0].sha // empty'); else sha=$REF; fi
[ -n "$sha" ] || { echo "no CI-built commit for $REF" >&2; exit 1; }
sha7=${sha:0:7}; echo "deploying $sha7 to $ESP"
j() { python3 -c 'import functools,json,sys; print(functools.reduce(lambda d,k: d[k], sys.argv[1].split("."), json.load(sys.stdin)))' "$1"; }
dev() { curl -sf --max-time 10 "$ESP/api/state" | j "device.$1" 2>/dev/null || true; }
ota() { curl -sf --max-time 10 -X POST "$ESP/api/ota" -d "{\"action\":\"$1\"}" >/dev/null || true; }
fail_on_error() { [ "$(dev ota.state)" != error ] || { echo "ota error: $(dev ota.error)" >&2; exit 1; }; }
curl -sf --max-time 10 -X POST "$ESP/api/ota" -d "{\"channel\":\"$chan\"}" >/dev/null || true
ch=$(dev ota.channel)   # empty: firmware from before the channels, which always uses esp-latest
if [ -z "$ch" ]; then
  [ "$chan" = latest ] || { echo "ESP firmware has no channel support yet: install main first" >&2; exit 1; }
  echo "note: ESP firmware has no channel support yet; it uses esp-latest"
elif [ "$ch" != "$chan" ]; then echo "channel not set" >&2; exit 1; fi
until [[ "$(gh release download "esp-$chan" -R "$REPO" -p manifest.json -O - | j sha)" == "$sha7"* ]]; do
  echo "waiting for esp-$chan manifest = $sha7"; sleep 15; done
until [[ "$(dev fw)" == "$sha7"* ]]; do   # check every 15 s until the ESP offers this sha, then install
  ota check; sleep 15; fail_on_error
  [ "$(dev ota.state)" = available ] && [[ "$(dev ota.available_sha)" == "$sha7"* ]] || continue
  ota install; until [[ "$(dev fw)" == "$sha7"* ]]; do sleep 5; fail_on_error; done; done
until [ "$(dev uptime_ms)" -gt 70000 ] 2>/dev/null; do sleep 5; done   # 60 s rollback window passed
# a crash inside that window makes the bootloader roll back to the old slot: that is a failed deploy
[[ "$(dev fw)" == "$sha7"* ]] || { echo "rolled back: device.fw = $(dev fw)" >&2; exit 1; }
for k in fw link ota; do echo "device.$k = $(dev $k)"; done

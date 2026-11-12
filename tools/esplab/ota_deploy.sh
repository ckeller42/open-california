#!/usr/bin/env bash
# OTA the ESP satellite to a build once CI's firmware-release has published it (R_FW_OTA).
#   tools/esplab/ota_deploy.sh [stable|main|sha] [esp-url]     e.g. ota_deploy.sh main http://192.168.8.183
# stable = the esp-stable tag's build (channel stable); main / a sha = a merge commit (channel latest).
# Sets the ESP's OTA channel, waits for esp-<channel>'s manifest.json to name the sha, checks until
# the ESP sees it (GitHub's CDN can lag ~1-2 min), installs, then waits for the new fw and its
# verdict (device.ota.verify: valid once the camper link was up, or 15 min without an advertising
# unit; failed after 40 min of an advertising unit without a link). It gives up after 90 min. Firmware from before the channels boots with no channel byte (= stable): the channel
# is then set again after the install. USB: flash_ci.sh.
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
# the verdict, read from ONE state reply per round (a failed read is retried, never taken for "no
# verify"): valid -> done; failed -> exit 1; a build without device.ota.verify confirms itself after
# 60 s of a healthy run. A crash before the verdict makes the bootloader boot the old slot.
deadline=$((SECONDS + 5400))
while :; do
  if st=$(curl -sf --max-time 10 "$ESP/api/state") && read -r f up v < <(printf '%s' "$st" | python3 -c \
      'import json,sys; d=json.load(sys.stdin)["device"]; print(d["fw"], d["uptime_ms"], d["ota"].get("verify", "none"))'); then
    [[ "$f" == "$sha7"* ]] || { echo "rolled back: device.fw = $f" >&2; exit 1; }
    [ "$v" = valid ] && break
    [ "$v" = none ] && [ "$up" -gt 70000 ] && break
    if [ "$v" = failed ]; then
      echo "verdict failed: $sha7 is never confirmed; the next reset boots the previous slot. No other OTA" >&2
      echo "runs until then. Roll back now: console 'ota invalid' (esp_cmd.py on the USB host) or a power cycle." >&2
      exit 1
    fi
  fi
  [ "$SECONDS" -lt "$deadline" ] || { echo "no verdict after 90 min (device.ota.verify = ${v:-unreadable})" >&2; exit 1; }
  sleep 15
done
if [ -z "$ch" ]; then   # pre-channel firmware: the new build booted on the default (stable), so set it again
  curl -sf --max-time 10 -X POST "$ESP/api/ota" -d "{\"channel\":\"$chan\"}" >/dev/null || true
  [ "$(dev ota.channel)" = "$chan" ] || { echo "channel not set after install: ota.channel = $(dev ota.channel)" >&2; exit 1; }
fi
for k in fw link ota; do echo "device.$k = $(dev $k)"; done

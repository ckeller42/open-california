---
name: ship-a-change
description: Use when any change is ready to leave your working tree in open-california (or buspi-config / californiaontour-re) — opening a PR, waiting on CI or CodeRabbit/claude-review, merging, rebasing over a generated-file conflict, deploying to buspi, or flashing the ESP satellite after a merge.
---

# Ship a change: PR → review loop → merge → deploy → flash

Merge yourself only when CI is green **and** every sensible bot comment is handled; then put the
merge commit on buspi and the ESP. Buspi mechanics (live reads, address, tailscale): `buspi-deploy`.

## Quick reference

| Step | Do |
|---|---|
| Work | Fresh scratch clone + branch, never the owner's checkout. `python3.13` (bare `python3` = 3.9). |
| Gate | `tools/ci.sh test`, `tools/ci.sh webcheck`, `tools/ci.sh lint`, `python3.13 -m pre_commit run --files …`. Never `--no-verify`. |
| Push | `git -c credential.helper='!gh auth git-credential' push` (MacPorts hang). End commits/PR body with the attribution lines. |
| Wait | `gh pr checks N --watch`, then read `gh pr view N --comments` + `gh api repos/ckeller42/open-california/pulls/N/comments`. |
| Bots | CodeRabbit rate-limited → comment `@coderabbitai review` later. claude-review infra failure → `gh run rerun <id>`. |
| Fix | Ponytail lens: real bug → fix; noise → one-line reply. Reply on every thread; `line: null` = outdated, maybe fixed. Loop. |
| Privacy | `gh pr diff N --name-only`: no images/app screenshots (private `californiaontour-re` only), captures, APK, decompile, VIN, unit MAC `20:81:9A…`. |
| Merge | `gh pr merge N --squash --delete-branch` (also buspi-config, californiaontour-re). |
| Deploy | `ssh pi@buspi 'cd ~/open-california && git pull && sudo systemctl restart calictl'`; verify `systemctl is-active calictl`, `git log -1`, `/api/state` on :8088. |
| Dashboard | After signal/enum changes: `push_dashboard.py` to Pi :3000 (`GRAFANA_PASSWORD`) and Cloud (`sudo -n bash -c 'set -a; . /etc/buspi/secrets.env; GRAFANA_TOKEN=$GRAFANA_CLOUD_TOKEN …'`; 503 → retry). Never echo secrets. |
| Flash ESP | If `firmware/`, `calictl/webui/**` or semantics changed (UI is bundled): below. |
| Offline | `until ssh -o ConnectTimeout=8 -o BatchMode=yes pi@buspi true; do sleep 60; done` in background; then deploy/flash, tell the owner. |

## Generated-file conflicts (rebase on `origin/main`)

Never hand-merge `app_bundle_gen.h`, `semantics.json`, control vectors, `screens.json`
(`git checkout --theirs <file>`), regenerate all, prove a second run is clean, re-gate:

```sh
python3.13 -m tools.build_web && python3.13 -m tools.gen_semantics_vectors \
  && python3.13 -m tools.gen_control_vectors && python3.13 -m tools.gen_c_dict
python3.13 -m tools.gen_c_dict --check && git add -A && git rebase --continue
git fetch origin    # lease = the remote sha you last saw
git push --force-with-lease=BRANCH:$(git rev-parse origin/BRANCH) origin BRANCH
```

## Flash the ESP from the merge commit

```sh
SHA=$(gh pr view N --json mergeCommit --jq .mergeCommit.oid)
RUN=$(gh run list -R ckeller42/open-california --workflow ci.yml --commit "$SHA" --json databaseId --jq '.[0].databaseId')
gh run watch "$RUN" --exit-status          # cancelled? gh run rerun "$RUN"
ssh pi@buspi "~/open-california/tools/esplab/flash_ci.sh $RUN /dev/ttyACM0"
curl -s http://<esp-ip>/api/state          # IP: esp_cmd.py … "wifi status"; device.fw == merge sha, device.link.up, a value (water)
```

The ESP hangs off buspi's USB (`/dev/ttyACM0`). Link timing: `tools/esplab/esp_tail.py /dev/ttyACM0 120`
(timestamped, no reset).

## Common mistakes

| Mistake | Fix |
|---|---|
| Plain `--force-with-lease` → "stale info" | Explicit lease `=BRANCH:<remote sha>` |
| `flash_ci.sh main` | Run id of the merge commit; the `[skip ci]` screenshot commit cancels/supersedes it and `main` flashes the previous build |
| UI change, bundle not regenerated → ESP serves old UI | Regenerate `app_bundle_gen.h` (`gen_c_dict`); `--check` it |
| Mutation check against the source, not the regenerated bundle | Break → regenerate → watch the test fail |
| Merged on green CI before reading bot comments | Read both comment endpoints first |
| `--delete-branch` on a base with stacked child PRs (closes them) | Retarget the children first |
| CodeRabbit check "pass" | Often means skipped (rate limit); look for its review, re-trigger |

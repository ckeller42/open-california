---
name: ship-a-change
description: Use when any change is ready to leave your working tree in open-california (or buspi-config / californiaontour-re) — opening a PR, waiting on CI or CodeRabbit/claude-review, merging, rebasing over a generated-file conflict, deploying to buspi, or updating (OTA) or USB-flashing the ESP satellite after a merge.
---

# Ship a change: PR → review loop → merge → deploy → update the ESP

Merge yourself only when CI is green **and** every sensible bot comment is handled; then put the
merge commit on buspi and the ESP. Buspi mechanics (live reads, address, tailscale): `buspi-deploy`.

## Quick reference

| Step | Do |
|---|---|
| Work | Fresh scratch clone + branch, never the owner's checkout. `python3.13` (bare `python3` = 3.9). |
| Gate | `tools/ci.sh ci` (= lint + tests + audit), `tools/ci.sh webcheck`, `~/Library/Python/3.13/bin/pre-commit run --all-files` (ALL files: CI runs the hooks that way; `--files` misses a reformat elsewhere) — **before** the push, then `git status`: a hook that reformats leaves the fix uncommitted, and the pushed commit fails CI's pre-commit. Never `--no-verify`. |
| CI-only gates | Firmware/C or anything near a secret: the three checks below that only CI runs — gcc `-Werror`, the gitleaks working-tree scan, `sphinx -W`. |
| Push | `git -c credential.helper='!gh auth git-credential' push` (MacPorts hang). End commits/PR body with the attribution lines. |
| Wait | `gh pr checks N --watch`, then read `gh pr view N --comments` + `gh api repos/ckeller42/open-california/pulls/N/comments`. |
| Bots | CodeRabbit rate-limited → comment `@coderabbitai review` later. claude-review infra failure → `gh run rerun <id>`. |
| Fix | Ponytail lens: real bug → fix; noise → one-line reply. Reply on every thread; `line: null` = outdated, maybe fixed. Loop. |
| Privacy | `gh pr diff N --name-only`: no images/app screenshots (private `californiaontour-re` only), captures, APK, decompile, VIN, unit MAC `20:81:9A…`. |
| Merge | Only when **every** check finished green — a check still pending is not green: `gh pr checks N --json name,bucket \| jq -e 'length>0 and all(.bucket=="pass" or .bucket=="skipping")' && gh pr merge N --squash --delete-branch` (also buspi-config, californiaontour-re). The `jq -e` gates the `&&`; printing a count does not. |
| Deploy | `ssh pi@buspi 'cd ~/open-california && git checkout main && git pull --ff-only && sudo systemctl restart calictl'` (never a detached sha); verify `systemctl is-active calictl`, `git log -1`, `/api/state` on :8088. |
| Dashboard | After signal/enum changes: `push_dashboard.py` to Pi :3000 (`GRAFANA_PASSWORD`) and Cloud (`sudo -n bash -c 'set -a; . /etc/buspi/secrets.env; GRAFANA_TOKEN=$GRAFANA_CLOUD_TOKEN …'`; 503 → retry). Never echo secrets. |
| Update ESP | If `firmware/`, `calictl/webui/**` or semantics changed (UI is bundled): OTA from the merge's `firmware-release`, below. |
| Live check | After the OTA (valid after 60 s, `device.link.up`): exercise the merged feature on the real ESP + real unit through the UI/API (and Influx for ingest). Report **verified** vs **still owed** (e.g. needs a drive). Host/QEMU/mock tiers alone are not enough; USB flash is recovery only. |
| Offline | `until ssh -o ConnectTimeout=8 -o BatchMode=yes pi@buspi true; do sleep 60; done` in background; then deploy/flash, tell the owner. |

## CI-only gates (green on the Mac, red in CI)

The Mac's clang and the staged-only hooks miss three things CI enforces. Run them before pushing
a PR that touches `firmware/` C, a `cali_core/test/*_cli.c`, a key/token-shaped literal, or docs:

```sh
# 1) the host test CLIs compile with gcc -Werror on Linux (clang skips -Wformat-truncation:
#    snprintf(buf, sizeof buf, "%s", tok) from a longer token → use a precision, "%.17s")
docker run --rm --platform linux/amd64 -v "$PWD":/repo -w /repo python:3.13 bash -c \
  'pip install -q pytest pyyaml; python -m pytest tests/firmware/test_sensors.py tests/firmware/test_web_handlers.py -q -p no:cacheprovider'
# 2) the gitleaks WORKING-TREE scan (manual stage; the commit hook scans only staged hunks):
#    a hex alphabet or any literal next to a word like key/token trips generic-api-key →
#    a named constant (static const char HEXDIGITS[] = "0123…") away from the word
~/Library/Python/3.13/bin/pre-commit run --hook-stage manual gitleaks-dir --all-files
# 3) docs: sphinx -W (an unbalanced ``literal`` in a docstring fails the docs job; sphinx is
#    not installed on the Mac)
docker run --rm --platform linux/amd64 -v "$PWD":/repo -w /repo python:3.13 bash -c \
  'set -euo pipefail; pip install -q -r docs/requirements.txt; python -m sphinx -b html -W --keep-going docs /tmp/out 2>&1 | grep -iE "warning|error|build succeeded"'
```

CI job logs are unreadable until the whole run completes (`gh run view --log` says "still in
progress"); reproduce locally instead of waiting ~25 min for the e2e jobs.

## Stacked PRs (the base is another PR's branch)

Open each PR with `--base <parent-branch>`; the repo **squash-merges**, so after the parent
lands the child's merge-base is stale (its diff re-shows the parent, bots re-review it). Before
the owner merges the next one:

```sh
OLD=$(git merge-base <parent-branch> <child>)   # where the child forked from the parent (NOT the parent's tip: it may have grown since)
git fetch origin main
git rebase --onto origin/main "$OLD" <child>
git push --force-with-lease=<child>:$(git ls-remote origin refs/heads/<child> | cut -c1-40) origin <child>
```

GitHub retargets the child to `main` by itself when the parent merges; never
`--delete-branch` a parent while children are open (it closes them). A hotfix while another
worktree has `<branch>` checked out: `git worktree add --detach /tmp/fix <branch>`, commit there,
`git push origin HEAD:<branch>` — never switch branches under a running implementer.

## Generated-file conflicts (rebase on `origin/main`)

Never hand-merge `app_bundle_gen.h`, `semantics.json`, control vectors, `screens.json`
(`git checkout --theirs <file>`), regenerate all, prove a second run is clean, re-gate:

```sh
python3.13 -m tools.build_web && python3.13 -m tools.gen_semantics_vectors \
  && python3.13 -m tools.gen_control_vectors && python3.13 -m tools.gen_c_dict
python3.13 -m tools.gen_c_dict --check && git add -A
if git grep -n -e '^<<<<<<< ' -e '^>>>>>>> '; then
  echo "UNRESOLVED conflict markers: fix them, git add, run this block again"
else
  git rebase --continue && git fetch origin \
    && git push --force-with-lease=BRANCH:$(git rev-parse origin/BRANCH) origin BRANCH   # lease = the remote sha you last saw
fi
```

`git add -A` stages a hand-written file **with** its conflict markers (a `docs/api.rst` slipped
into a commit that way): grep for them before `--continue`. When both sides added different
tests or doc lines at the same spot, keep both.

## A firmware host-tier test that fails only in CI (flaky)

The host tier (NimBLE on a Bumble link, Linux only) runs on the Mac in Docker. Reproduce under
load, loop until it fails, and make the failure print the firmware's own log — the assertion
alone shows only the end state:

```sh
docker run --rm --cpus=0.35 -v "$PWD":/w -w /w -e CROSS_COMPILE=i686-linux-gnu- \
  -e QEMU_LD_PREFIX=/usr/i686-linux-gnu -e CALI_REQUIRE_CHROMIUM=1 oc-fw-host-pw bash -c \
  'for i in $(seq 1 20); do python3 -m pytest tests/firmware/<file>.py -vv -p no:cacheprovider -k "<test>" \
     > /tmp/run.txt 2>&1; grep -q "1 failed" /tmp/run.txt && { cat /tmp/run.txt; break; }; tail -1 /tmp/run.txt; done'
```

In the test, print `fw.log[mark:]` (the `LOG`/`STATE` lines) on failure; add `cali_log` lines at
the suspect transport branch if needed. `-q -vv` cancel out: use `-vv` alone. #381 was found this
way (a connect refused with `BLE_HS_ENOMEM` while the session's own link was still dying).

## Update the ESP from the merge commit (OTA)

CI's `firmware-release` job (push to `main` only) puts the merge commit's `cali_fw.bin` +
`manifest.json` on the prerelease `esp-latest`; the ESP installs it on request (`R_FW_OTA`,
`docs/firmware.md` "Firmware update from GitHub"):

**Two channels** (PR #365): `esp-latest` = every `main` merge; `esp-stable` = the build the moving
git tag `esp-stable` points at (published by the `workflow_dispatch` workflow `esp-stable.yml`).
The ESP picks its channel (NVS `ota_channel`, default stable; `/device` → Firmware → Channel, or
`POST /api/ota {"channel":"stable"|"latest"}`; `device.ota.channel`).

| Want | Do |
|---|---|
| Install a merge | `tools/esplab/ota_deploy.sh main` (or `<sha>`) — sets channel `latest`, waits for `esp-latest` to name the sha, installs, waits for `device.fw` + the 60 s rollback window, fails on `ota.state` `error` or a rollback. |
| Promote the soaked build | `tools/esplab/promote_stable.sh <sha>` after a green 72 h soak report (`tools/esplab/soak_report.py`, daily `calictl-esp-soak.timer` on buspi → `~/esp-soak/<date>.txt`, `VERDICT PASS`): moves the tag, dispatches the workflow (needs that sha's `main` CI artifact, ≤ 90 days old). |
| Fall back | `tools/esplab/ota_deploy.sh stable` — switches the channel, installs the `esp-stable` image. On pre-channel firmware (no `device.ota.channel`) only `main` works; the script re-sets the channel after that first install. |
| The gate | No install on the van ESP before the running build's 72 h report is green; a new build runs 72 h with `exp_sensors` off, then 72 h on, before promotion. |

By hand (latest channel):

```sh
SHA=$(gh pr view N --json mergeCommit --jq .mergeCommit.oid)
RUN=$(gh run list -R ckeller42/open-california --workflow ci.yml --commit "$SHA" --json databaseId --jq '.[0].databaseId')
gh run watch "$RUN" --exit-status          # firmware-release must pass; cancelled? gh run rerun "$RUN"
curl -s -X POST http://<esp-ip>/api/ota -d '{"action":"install"}'   # or /device → Install <sha7>
curl -s http://<esp-ip>/api/state          # device.ota: downloading → rebooting; then device.fw == merge sha, device.link.up
```

`device.ota.state` `up_to_date` = already running it; `error` + `checksum` = a newer merge replaced
the release mid-download (install again). Station mode only (`403` over the setup hotspot). The new
build marks itself valid after 60 s healthy; a crash before that boots the previous slot.

**The ESP lost its WiFi** (a failed network change on old firmware, a wrong password typed on
the hotspot) and sits on its setup hotspot, out of reach: buspi holds the same network in its
own NetworkManager profile and the ESP on its USB. `ssh buspi 'sudo python3
~/open-california/tools/esplab/esp_wifi_restore.py [PROFILE]'` reads the profile (root-only) and
sends `wifi set` over `/dev/ttyACM0` — the password never appears in argv, chat or output. Nobody
has to be at the van. (Since PR #394 a failed change rolls back by itself in ~10 s.)

**USB recovery path** (`flash_ci.sh`; both slots broken, or the first flash of a new partition layout): the ESP on
a laptop's/buspi's USB, then `tools/esplab/flash_ci.sh <run-id> /dev/ttyACM0` (bootloader, partition
table, `ota_data_initial.bin`, app at `ota_0`; NVS untouched). Link timing over USB:
`tools/esplab/esp_cmd.py /dev/ttyACM0 --tail 120` (timestamped, no reset).

## Common mistakes

| Mistake | Fix |
|---|---|
| Plain `--force-with-lease` → "stale info" | Explicit lease `=BRANCH:<remote sha>` |
| `flash_ci.sh main` | Run id of the merge commit; the `[skip ci]` screenshot commit cancels/supersedes it and `main` flashes the previous build |
| OTA before `firmware-release` finished | The manifest still names the previous merge (`up_to_date` / old sha); wait for the job, then install |
| UI change, bundle not regenerated → ESP serves old UI | Regenerate `app_bundle_gen.h` (`gen_c_dict`); `--check` it |
| Mutation check against the source, not the regenerated bundle | Break → regenerate → watch the test fail |
| Merged on green CI before reading bot comments | Read both comment endpoints first |
| `--delete-branch` on a base with stacked child PRs (closes them) | Retarget the children first |
| CodeRabbit check "pass" | Often means skipped (rate limit); look for its review, re-trigger |
| Local gate green, CI `test`/`pre-commit`/`docs` red | A CI-only gate: gcc `-Werror`, gitleaks working tree, `sphinx -W` (section above) |
| `ota_deploy.sh main` right after a merge | `firmware-release` must finish first (the manifest still names the previous merge) |
| `ota_deploy.sh main` waits for ever | `main`'s head is the `[skip ci]` screenshot commit: no release names it. Pin the merge sha: `ota_deploy.sh <merge-sha7>` |
| Merged while a check was still pending | Gate the merge on `jq -e 'all(pass\|skipping)'` (Quick reference), not on a printed count |
| Pushed, then pre-commit reformatted | Run pre-commit and check `git status` before the push; amend, push again |
| Promoting a sha whose CI artifact expired (> 90 d) | `esp-stable.yml` fails loudly; re-run that commit's `main` CI or promote a newer sha |
| `git`/`cc` print "You have not agreed to the Xcode license" (`checkout -b` silently did nothing, commit lands on `main`) | `export PATH=/opt/local/bin:$PATH` (MacPorts git), push with the `gh auth git-credential` helper above, check `git branch --show-current` before pushing |

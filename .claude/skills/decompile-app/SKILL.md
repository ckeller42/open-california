---
name: decompile-app
description: Decompile and analyze any (obfuscated) Android app to read its real logic — extract a protocol/field map, or answer "what does the app actually do" questions from its source. Builds a durable rename + doc-comment mapping that survives re-decompiles, kept in a private per-app analysis repo. Use to reverse-engineer an app's BLE/network/control behaviour and reconcile it with live captures. The CaliforniaOnTour camper app is the worked example at the end.
---

# Decompile & analyze an Android app (the ground truth)

An app's own bytecode is the authority on the protocol/behaviour it drives. Live captures show
*what* bytes go on the wire; the decompiled app shows *why* — the field layout, enums, and the
control flow that picks each frame. Reach for this when a live observation needs reconciling with
the app's intent, or to extract a field map. This skill is app-agnostic; a concrete worked example
(the VW CaliforniaOnTour camper app) is in the last section.

**LEGAL / PRIVACY — HARD RULES.** A third-party APK and everything decompiled from it are the
vendor's copyright; this is for **own-device interoperability research**. Therefore:
- **Never commit decompiled sources (or the APK/DEX) into the host project's repo**, and never into
  any *public* repo. Keep them in a **separate, PRIVATE** per-app analysis repo (below).
- In the host project's docs, **citations only** (`class.java:line`), never pasted vendor source.
- Verify `--private` at repo-create time; a public push is a licensing violation, not a mistake to
  fix later.

## Pick your parameters (everything below is parameterised)

```sh
PKG=de.volkswagen.CaliforniaOnTour           # the app's package id
APP=californiaontour                          # short slug for paths
WORK=~/apks/$APP                              # APK + extracted DEX
OUT=~/src/$APP-decompile                      # analysis workspace (its own private git repo)
```

Run heavy Java on a box with RAM to spare and cap the heap (`-Xmx`); don't run two decompilers at
once on a small host. buspi (aarch64/Debian, ~3.8 GB) is fine one-at-a-time.

## One-time tooling (idempotent)

```sh
sudo apt-get install -y default-jdk-headless        # jadx/Vineflower/apktool need Java 17+
mkdir -p ~/tools && cd ~/tools
gh_latest() { curl -s "https://api.github.com/repos/$1/releases/latest" \
  | grep browser_download_url | grep -E "$2" | grep -v gui | head -1 | cut -d'"' -f4; }
curl -sL "$(gh_latest skylot/jadx        'jadx-[0-9].*\.zip')"        -o jadx.zip && unzip -qo jadx.zip -d jadx
curl -sL "$(gh_latest pxb1988/dex2jar    'dex-tools.*\.zip')"          -o d2j.zip  && unzip -qo d2j.zip -d dex2jar && chmod +x dex2jar/*/*.sh
curl -sL "$(gh_latest Vineflower/vineflower 'vineflower-[0-9].*\.jar')" -o vineflower.jar
curl -sL "$(gh_latest iBotPeaches/Apktool 'apktool_[0-9].*\.jar')"      -o apktool.jar   # bundles baksmali
# apkeep (fetch APKs) — cargo install apkeep, or grab a release binary for the arch
```

Fetch + unpack an APK: `apkeep -a "$PKG" -d apk-pure "$WORK"` then
`unzip -o "$WORK/$PKG.apk" 'classes*.dex' -d "$WORK/dex"`.

## Decompile — three tools, three fidelities

**jadx (primary, DEX→Java, Kotlin-aware):**
```sh
JAVA_OPTS=-Xmx2g ~/tools/jadx/bin/jadx --show-bad-code --no-res -j 2 \
  --rename-mappings "$OUT/mapping.jobf" \
  -d "$OUT" "$WORK"/dex/classes*.dex
```
- **`--show-bad-code` is REQUIRED.** Without it jadx *silently* skips large/obfuscated methods,
  printing `"Method dump skipped, instruction units count: N"` — you lose exactly the dense
  state-machine methods you most need. Always pass it.
- `--rename-mappings <file>` applies your durable rename+doc layer (next section) on every run.
- Long job; run detached (`setsid … </dev/null >log 2>&1 &`) so an ssh drop doesn't kill it; poll
  the log for a completion marker.

**Vineflower (second Java view — cleaner on some Kotlin/coroutine constructs):**
```sh
~/tools/dex2jar/*/d2j-dex2jar.sh -f -o ~/tools/$APP.jar "$WORK"/dex/classes*.dex
java -Xmx2g -jar ~/tools/vineflower.jar ~/tools/$APP.jar "$OUT/vineflower-src"
```
Use it to cross-check a method jadx renders oddly — coroutine state machines especially.

**smali (baksmali, via apktool — ground truth):**
```sh
java -Xmx1500m -jar ~/tools/apktool.jar d -r -f "$WORK/$PKG.apk" -o "$OUT/smali"   # -r => no aapt
```
smali never lies; decompilers guess. When both Java views disagree or emit "bad code", read the
`.smali` and hand-trace the bytecode.

## The durable deobfuscation layer (renames + doc-comments that survive re-decompiles)

R8 renames everything to `a.b`, `E()`, `d0()`. The fix is a **jadx mappings file** you own and
re-apply on every decompile, so meaningful names + docs are never lost:

- **Format:** jadx reads/writes Enigma / Tiny2 mappings and its own `.jobf`. Pick one (Enigma is
  human-diffable) and keep it at `$OUT/mapping.<fmt>`. It carries **both** renames *and* javadoc
  comments — jadx reapplies the comments into the regenerated source.
- **Author it two ways (A+C):**
  - **A — hand-curate** the load-bearing classes as you understand them (`dg.h`→`LightingControl`,
    `E`→`setZoneBrightness`, `ag.b`→`LivenessCounter1003`), adding a one-line doc-comment on each.
  - **C — seed at scale with an LLM pass:** dispatch an agent over the fresh sources to infer names
    + short doc-comments from method bodies, string constants, and any plaintext debug-log methods,
    emitting mapping entries; then hand-curate the ones that matter. Re-run as coverage grows.
- **Editing interactively:** jadx-gui rename (`n`) + "Add comment" persist into the mappings file
  ("Save mappings as…"); the headless runs then pick them up. Keep the file in the private repo.
- Result: `git diff` after a vendor app update shows what actually changed in *named* terms.

## The private per-app analysis repo

```sh
cd "$OUT" && git init
printf '*.apk\n*.dex\n*.jar\n' > .gitignore          # binaries are huge + regenerable; never commit them
git add -A && git commit -m "decompile + mapping: $APP"
gh repo create "$APP-re" --private --source=. --push  # PRIVATE — verify, never public
```
Commit: the decompile/apply script, the `mapping.*` file, the renamed+annotated source tree, and
analysis notes. Gitignore the APK/DEX/intermediate jars. One private repo per app.

## Reading an obfuscated app — general technique

1. **Find the plaintext anchors.** Obfuscators rarely encrypt *log* strings — debug/telemetry
   methods often name fields/opcodes in the clear (`"--> Sending Data"`, `"<-- Incoming …"`). String
   tables, resource names, and characteristic UUIDs (literal in BLE apps) are anchors too.
2. **Follow the anchor to the model.** The method that logs the fields usually also *places* them
   (bit slices / `subList`, `@n` offsets, enum ordinals). That's your field map without cracking any
   string decryptor.
3. **Cross-check** anything load-bearing across jadx ↔ Vineflower ↔ smali before asserting; then
   reconcile against a live capture (see the `capture-and-diff` skill) and, for actuation, physical
   observation — never trust a single decompiler or a readback echo.
4. **Record** each confirmed name/behaviour back into the mapping file so the next decompile is more
   readable than the last.

---

## Worked example — CaliforniaOnTour (VW camper, this project)

- `PKG=de.volkswagen.CaliforniaOnTour`, on **buspi**: APK `~/apks/de.volkswagen.CaliforniaOnTour.apk`
  (+ `~/apks/dex/classes{,2}.dex`), tools in `~/tools/`, analysis repo `~/src/californiaontour-decompile/`.
- **Class map (verified against the fresh source 2026-08-16):**

| Area | Class:method | Note |
|---|---|---|
| Lighting control | `dg/h.java` | `E()` set-zone (:174, stages PN=9 + Mode=4, writes DIRECT), `Q()` all-lights (:323 SET_PROFILE PN 12/0), `d0()` REQUEST_CONFIG (:471, screen-open config pull — **NOT** called by `E()`), `u0()` activate profile |
| Lighting enums | `dg/n` Mode · `dg/i` brightness · `dg/l`+`ef/k` profile# · `dg/j` colour · `dg/k` wake mode | |
| Lighting state decode | `dg/a.java` | needs `--show-bad-code` |
| Send mode | `qf/b.java` | `DIRECT`(0)=transmit now, `PENDING`(1)=stage locally (optimistic preview) |
| 1003 liveness counter | `ag/b.java` (`"00001003-…F3D"`) | connection-liveness object, **separate** from the write path |
| Lighting state char | `eg/b.java` (`"00001502-…F3D"`) | observed for state/notifications |
| Roof | `ig/c.java` | state decode (bad-code); move-heartbeat `:674,:764` (`jn.a(1000L…)` 1 Hz) |
| GATT op class | `td/c.java` (extends `td/b`) | only the 0x1000 service wired with literal UUIDs |

- **Feeds:** `tools/extract_protocol.py <OUT>/sources protocol/dictionary.yaml` regenerates the
  dictionary (13 functions); then the coverage guardrail + `add-signal` skill. For behaviour
  questions, grep the method, read it, cross-check the enum, cite `class.java:line`.
- See `docs/business-logic/lighting-energy-water-sat-roof.md` (full method map) and memory
  `californiaontour-apk-decompile`. Key finding 2026-08-16: `E()` never calls `d0()`, so the
  REQUEST_CONFIG preamble is not a per-write actuation gate — the app just writes DIRECT.

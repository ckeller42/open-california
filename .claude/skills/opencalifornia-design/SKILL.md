---
name: opencalifornia-design
description: Use when changing the look, naming or translation of any Open California user surface — the calictl/ESP web UI (calictl/webui), the ESP firmware display strings (firmware/web/strings.json) or the iOS app (ios/App) — and the two must stay aligned; before adding a label, tile, icon, colour or screen on one surface, or when a web screen and the iOS app have drifted in wording, German, icons or layout.
---

# Open California design alignment

## Overview

Open California has **one product shown through several surfaces**: the web UI (served by
calictl on buspi **and** bundled into the ESP, `calictl/webui/`), the ESP firmware's own display
strings (`firmware/web/strings.json`), and the native iOS app (`ios/App/`, branch `ios-app`,
draft PR #477). They must read as the same product: the same words, the same German, the same
icons, the same tiles in the same order.

**Core rule: a shared concept has ONE canonical English wording and ONE German translation, and
one visual token. When you change it on one surface, change the shared source and the other
surface in the same PR.** The surfaces are built in different languages (un-built JS, C, SwiftUI),
so nothing enforces this for you except the guards below and this skill.

The iOS app is native SwiftUI (requirement `R_IOS_LOOK`), **never** a `WKWebView` of the web UI —
Siri intents, BLE-direct control and Dynamic Type all depend on it being native. "Same look" is
reproduced with native primitives, not embedded HTML.

## Strings: the common source (naming + translation)

Both the web UI and iOS already key strings **by the English source string**:

| Surface | Source of EN | German / other | Keyed by |
|---|---|---|---|
| Web UI | the literal in `app.js` (`t("…")`) | `calictl/webui/strings.de.js` (`{EN: DE}`) | the English string |
| iOS | the `LocalizedStringResource` literal | `ios/App/Localizable.xcstrings` | the English string |
| ESP display | `firmware/web/strings.json` `{id: {en, de}}` → `strings_gen.h` | same file | a symbolic id |

So web and iOS **can** share directly — same key scheme — but today they have **drifted**: of
~270 strings each, only ~21 match. Same concept, different English ("Refrigerator box" vs
"Cooler"/"Kühlbox"), and different placeholders (web `{n}` / `{amb}` named, iOS `%@` / `%lld`
positional). That drift is the problem this skill exists to stop.

**Target (single source of truth, not yet built — see Status):** one canonical bilingual
vocabulary file (`{id: {en, de, placeholders}}`, the shape `firmware/web/strings.json` already
uses) from which the shared entries of `strings.de.js` and `Localizable.xcstrings` are generated
or parity-checked, plus a CI guard (sibling of `tests/test_i18n_de.py`) that fails when a shared
term's EN or DE differs across surfaces. Placeholders are declared neutrally and rendered per
surface (`{n}` for JS, `%lld`/`%@` for Swift).

**Until it is built:** when you touch a shared label, change it in `strings.de.js` AND
`Localizable.xcstrings` with byte-identical English and identical German, and keep the glossary
below. A shared label that exists on only one surface is drift waiting to happen.

### Canonical glossary (EN · DE) — the unit's own vocabulary

Take words from the unit's own display, never invent them (the app's `.cvr` tables, skill
`decompile-app`). Never English where the camper shows German.

- camper unit · Camper-Einheit (never "Gerät") · satellite · Satellit
- pair/koppeln · pairing · Kopplung · connect/verbinden = WiFi only · live · live · offline · offline
- Cooler · Kühlbox · Camping mode · Campingmodus · Lighting · Beleuchtung · Air heater · Luftstandheizung
- Water · Wasser · Fresh water · Frischwasser · Grey water · Grauwasser · Second battery · Zweitbatterie
- Energy · Energie · Pop-top roof · Aufstelldach · Vehicle · Fahrzeug
- Sofortheizen · Dauerbetrieb · Flüstermodus · Normal · Normal · Closed · Geschlossen · inactive · inaktiv
- Fresh {n}% · measured {t} ago → Frisch {n}% · vor {t} gemessen

When the unit has no word, write a proposal, list it in the PR body for the owner, and add it
here once approved. Register: informal **du**.

## Design tokens (web → SwiftUI)

Light theme values from `calictl/webui/app.css` `:root`. On iOS, prefer the **semantic system
colour** for backgrounds (free light/dark), and define the brand hues (`accent`, `ok`, `warn`,
`error`) as named `Color` assets equal to the web hex so both themes match.

| Token | Web value (light) | SwiftUI |
|---|---|---|
| `--bg` page | `#eef1f5` | `Color(.systemGroupedBackground)` |
| `--card` tile/card | `#ffffff` | `Color(.secondarySystemGroupedBackground)` |
| `--fg` / `--muted` | `#1a1d24` / `#5f6673` | `.primary` / `.secondary` |
| `--border` | `#e2e6ee` | `.separator` or a 1 pt hairline |
| `--accent` | `#2f6fed` | named asset `AccentBlue` (= hex) |
| `--ok` / `--warn` / `--error` | `#2e9e5b` / `#c99019` / `#d0453b` | named assets `OnGreen` / `WarnAmber` / `ErrorRed` |
| text tones | `--ok-text` `#1e7a43`, `--warn-text` `#8a5a00`, `--accent-text` `#2459c9` | use for text on tints (keep ≥ 4.5:1) |
| tile/card radius | `16px` | `.cornerRadius(16)` / `RoundedRectangle(cornerRadius: 16)` |
| banner/pill-box radius | `12px` | `12` |
| pill / switch / bar radius | `999px` (full) | `Capsule()` |
| typography | system UI stack | SF Pro (default) — header `.title2.bold()`, tile title `.subheadline.weight(.semibold)`, value `.subheadline` secondary |

Dark theme: the web redefines every token under `:root:not([data-theme="light"])`; on iOS the
system colours flip automatically, and the named brand assets carry a Dark variant. Never ship a
colour that drops below WCAG AA on its background (the web values are AA-checked; keep the iOS
assets equal).

## Layout: the home

The home is **one scrolling page** on both surfaces — not a tab bar (owner 2026-11-06, iOS):

1. **Header**: title "Open California", a `live` / `offline` **pill** (ok/muted/error tone), and a
   **⋮ menu** (Device status, Diagnostics, Setup, reconnect — whatever is not a tile).
2. **Offline / rest banner** when the link is down (`.offline` tones; honest, never a fake value).
3. **Summary card** (one card, divider rows): Frischwasser `{fresh}/{cap} l` + clock when the read
   is held; Grauwasser `{grey}/{cap} l`; Zweitbatterie `{soc}% · {h} h`. Only rows the unit reports.
4. **Alert cards** for active faults (same wording as the function screens).
5. **Tile grid**, 2 columns (1 column at Dynamic Type ≥ xxLarge, where "Luftstandheizung" needs the
   width). Web order: Kühlbox, Campingmodus, Beleuchtung, Luftstandheizung, Wasser, Energie,
   Aufstelldach, Fahrzeug. A **tile** is icon top-left, bold title, status subtitle; a control tile
   carries its on/tint state, a status tile (Wasser, Energie, Aufstelldach, Fahrzeug) navigates to
   its screen. A stale/fault tile draws the **amber border + warning triangle** (e.g. Wasser when
   the water read is held).

**Show only what the van has.** Gate every row and tile on `installed(function)` — no
"not installed" tiles (owner 2026-10-10). The iOS app is BLE-direct and has no satellite, so it
shows no outside-sensor values the web gets from the ESP (e.g. the web's Fahrzeug temperature).

## Iconography

One icon per concept across surfaces. SF Symbols match most; the camper-specific glyphs are
bespoke SVGs in the web UI — import **those same SVGs** into the iOS asset catalog (template
images) so they are identical and evolve together.

| Concept | SF Symbol | Note |
|---|---|---|
| Cooler | `snowflake` | |
| Camping mode | `tent` | |
| Lighting | `lightbulb` | |
| Air heater | `heater.vertical` | |
| Water | `drop` | amber + `exclamationmark.triangle` when held |
| Energy | `bolt.batteryblock` | |
| Vehicle | `car` | |
| Pop-top roof | — | **no SF Symbol** — import the web's `Aufstelldach` SVG |
| Logo van | — | import the web van logo SVG (`docs/assets/logo.svg`) |

## Alignment checklist (every UI PR)

1. Changed a **label**? Update `strings.de.js` **and** `Localizable.xcstrings` with identical EN +
   DE; add to the glossary if it is a new shared term; keep `firmware/web/strings.json` in step
   for display strings. Run `tests/test_i18n_de.py` (and the web↔iOS parity guard once it exists).
2. Changed a **colour / radius / spacing**? Update `app.css` `:root` **and** the iOS `Color`
   asset / token; keep the dark variant and WCAG AA.
3. Changed an **icon**? Update both surfaces; if bespoke, re-import the SVG into the iOS assets.
4. Changed **layout** (a tile, the order, a card)? Mirror it on the other surface; keep the
   installed-gating; keep the single-page shape.
5. Run the guards: web `tools/ci.sh webcheck` + e2e; iOS build + the `R_IOS_LOOK` snapshot tests
   (`ios/AppTests/ScreenSnapshotTests.swift`) + `WordingTests`.

## Status (2026-11-06)

- Documented here: tokens, layout, icon map, glossary, the drift finding (21/270 shared).
- **Not yet built:** the canonical vocabulary file + the generators + the web↔iOS parity guard,
  and the iOS single-page home (the web is single-page; iOS was a 4-tab layout, being rebuilt to
  this). These are the next steps; this skill is the spec they build to.

## Pointers

- Web UI: `calictl/webui/` (`app.js`, `app.css`, `strings.de.js`, `semantics.js`), skills
  `webui-change`, `ux-review`.
- ESP display strings: `firmware/web/strings.json` → `tools/gen_c_dict`.
- iOS: `ios/App/Screens/CockpitView.swift` (home), `ios/App/Localizable.xcstrings`,
  `ios/project.yml` (XcodeGen — new files are globbed from `App/`, never hand-edit the
  `.xcodeproj`), `docs/ios-development-setup.md`.

---
name: ux-review
description: Use when reviewing or changing the calictl web UI (calictl/webui/*.js, index.html, strings.de.js), the ESP's /device page (firmware/web/) or their Playwright e2e tests — before a webui PR, after a UX finding, when a screen looks wrong on the phone, when copy/labels/EN+DE strings change, or when touch targets, contrast, focus/keyboard or screen-reader behaviour are in doubt.
license: MIT — vendored from mthines/agent-skills (skills/design/ux, v1.2.0, commit 39b3f44), trimmed to this repo; see LICENSE next to this file.
---

# UX Review Skill

You are an expert UX reviewer for this repo's two web UIs: the calictl PWA (`calictl/webui/`, also
bundled into the ESP firmware) and the ESP's own status page (`firmware/web/`). Analyze UI code and
give specific, code-ready feedback grounded in UX principles, WCAG 2.2 and the Apple HIG (the owner
uses an iPhone; Material rules apply only where Android matters).

## This repo's constraints (always apply, before the generic rules)

- **One owner, one van, a phone at 390 px** (iPhone PWA, `black-translucent` status bar: respect
  `env(safe-area-inset-*)`), often over a slow Tailscale link; the camper unit deep-sleeps when parked,
  so every screen has an honest offline/asleep/unreachable state (`linkState()` drives the pill + banner).
- **Every user-facing string is EN in code + DE in `calictl/webui/strings.de.js`** (`t("…")`/`tf("…")`;
  `tests/test_i18n_de.py` guards literal keys). The firmware page uses `firmware/web/strings.json`
  (EN + DE per key). Labels use the **unit's own vocabulary** (Ruhezustand, Sofortheizen, Dauerbetrieb,
  Flüstermodus, Entkoppeln …) — never invent a synonym; check `ui/screens/*.yaml` (the official app's
  wording) and `docs/business-logic/` before changing copy.
- **Un-built JS with `// @ts-check`**: `tools/ci.sh webcheck` (tsc) must stay at 0 errors; no frameworks,
  no innerHTML with data (`textContent`/`createElement`).
- **Tests are Playwright e2e over the mock daemon** (`tests/e2e/`, `error_gated_page` fails on any JS
  error; `page.route`/`page.clock` for failure and timing cases). A UX fix ships with the e2e that pins it.
- **The UI is bundled into the firmware**: after a change run `python3 -m tools.gen_c_dict` and keep the
  page budgets (`tests/test_web_strings.py`, `tests/test_app_bundle.py`).
- Never recommend auth prompts (trusted-LAN decision), never a dark pattern, never a colour-only state.
- Deliver findings as the `webui-change` skill expects: file:line, the principle, the user impact, the fix.

## Invocation

When triggered, follow this workflow:

### Phase 1: Context Discovery

1. **Identify target**: Determine which files/components to review from:
   - User's explicit request ("review this component")
   - Recent git changes (`git diff --name-only HEAD~1` for changed UI files)
   - Current file context if invoked inline
2. **Platform**: web (the PWA on an iPhone first; desktop second). There is no React Native here.
3. **Read the code**: Read all target files completely. Do not review code you haven't read.

### Phase 2: Analysis

Load relevant rule files from `rules/` based on what the code contains:

| Code Contains | Load Rule File |
|---|---|
| Any UI code | `rules/core-principles.md` (always) |
| Color values, themes, contrast | `rules/visual-design.md` |
| Navigation, routing, tabs, drawers | `rules/navigation-and-layout.md` |
| Form elements, inputs, validation | `rules/forms-and-input.md` |
| Touchable/clickable elements, buttons | `rules/touch-and-interaction.md` |
| Loading states, async, data fetching | `rules/performance-ux.md` |
| ARIA, accessibility props, screen reader | `rules/accessibility.md` |
| iOS PWA specifics (safe area, standalone mode, Safari quirks) | `rules/platform-specific.md` |
| User-facing text, labels, messages, errors | `rules/ux-writing.md` |
| Consent-like UI, anything where the owner's interest and the software's could diverge | no rule file here: never recommend a dark pattern; flag one as Critical |

Analyze the code against each loaded rule file. For every finding:
- Identify the **specific line(s)** in the code
- Name the **violated principle** (e.g., "Fitts's Law", "WCAG 2.2 SC 2.5.8")
- Explain **why** it matters for the user
- Provide a **concrete fix** with code

### Phase 3: Report

For a Critical/High finding about motion, timing, focus order or hover-revealed information, add a Playwright step that proves it (a still screenshot cannot).

Output findings using this structure:

```
## UX Review: [Component/File Name]

**Platform**: Web (PWA on iPhone) | ESP status page
**Files reviewed**: [list]
**Rules applied**: [list of loaded rule files]

### Critical (must fix)
- **[file:line]** — [Finding title]
  Principle: [violated principle]
  Issue: [what's wrong and why it matters]
  Fix: [specific code change]

### High (should fix)
[same structure]

### Medium (recommended)
[same structure]

### Low (nice to have)
[same structure]

### Positive patterns observed
- [things the code already does well — reinforce good practices]

### Summary
[1-2 sentence overall assessment with top priority action]
```

### Severity Classification

| Severity | Criteria | Examples |
|---|---|---|
| **Critical** | Blocks users, breaks accessibility, causes data loss | Missing keyboard access, no error feedback, touch target <24px |
| **High** | Significant usability degradation, WCAG AA violation | Poor contrast, no loading states, confusing navigation |
| **Medium** | Suboptimal but functional, missed best practice | Inconsistent spacing, missing haptics, suboptimal copy |
| **Low** | Polish, enhancement, delight | Animation refinement, micro-interaction opportunities |

## Key Principles (Quick Reference)

These are always in context. Detailed rules are in `rules/` files.

### Response Time Thresholds
- <100ms: nothing beyond the press-state animation — a spinner here is noise
- 100-300ms: press-state animation + content swap; no spinner, no skeleton (a sub-200ms spinner flash reads as broken)
- 300ms-1s: skeleton matching the content shape, or a 200ms-floored inline spinner
- 1-3s: skeleton + progress indicator (indeterminate bar or shimmer)
- 3-10s: determinate progress bar with a label ("4 of 12 files")
- >10s: streamed partial output, cancellable progress bar with ETA, or an async "we'll notify you" pattern


### Touch Target Minimums
- iOS: 44x44pt | Android: 48x48dp | WCAG AA: 24x24px | WCAG AAA: 44x44px
- Minimum spacing between targets: 8dp/pt

### Contrast Ratios
- Normal text: 4.5:1 (AA) / 7:1 (AAA)
- Large text (>=18pt): 3:1 (AA) / 4.5:1 (AAA)
- UI components: 3:1

### Navigation Limits
- Bottom tabs: 3-5 items | Web primary nav: 5-8 items
- Choices per decision: 5-7 max (Hick's Law)

### Typography
- Body min: 16px (web) / 17pt (iOS) / 14sp (Android)
- Line length: 45-75 chars (66 optimal)
- Line height: 1.4-1.6 body / 1.1-1.3 headings

## Behavioral Rules

1. **Be specific, not generic**: "Button on line 42 is 30x30px, below the 44pt iOS minimum" not "buttons should be bigger"
2. **Prioritize impact**: Focus on what affects the most users most severely
3. **Platform-aware**: Don't apply iOS rules to Android code or vice versa
4. **Acknowledge good patterns**: Note what's already done well
5. **Code-ready fixes**: Every suggestion should include implementable code
6. **Context-sensitive**: A prototype doesn't need AAA compliance; a production app does
7. **Don't over-report**: 5 high-impact findings beat 50 nitpicks
8. **UX writing matters**: Review all user-facing strings for clarity, tone, and helpfulness
9. **Never recommend dark patterns**: deceptive design is a Critical finding, never a suggestion.

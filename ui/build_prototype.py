#!/usr/bin/env python3
"""Generate a self-contained, clickable HTML preview of calictl's independent
camper hardware-control interface from ``ui/screens/*.yaml`` screen specs.

The interface is derived from the camper's BLE hardware surface (one screen per
installed subsystem, controls dictated by each function's protocol fields), not
copied from the vendor app's visual design. See ``docs/UI-DESIGN-RATIONALE.md``.

Usage:
    python3 ui/build_prototype.py

Reads every YAML file in ``ui/screens/`` (schema documented in the repo's
task brief / README), optionally cross-references ``protocol/dictionary.yaml``
for field metadata, and writes a single self-contained file:
``ui/prototype.html``.

The output has no external network dependencies (no external CSS/JS/fonts,
no remote images) so it can be opened directly in a browser or published as
a static artifact behind a strict Content-Security-Policy.

Requires PyYAML. If it isn't installed, this script prints an install
hint and exits non-zero rather than hand-rolling a YAML parser.
"""
from __future__ import annotations

import html
import json
import re
import sys
from pathlib import Path
from typing import Any

try:
    import yaml
except ImportError:
    print(
        "error: PyYAML is required to run this generator.\n"
        "Install it with:\n"
        "    pip install pyyaml\n"
        "or, inside this repo's virtualenv:\n"
        "    .venv/bin/pip install pyyaml\n",
        file=sys.stderr,
    )
    sys.exit(1)


ROOT = Path(__file__).resolve().parent
SCREENS_DIR = ROOT / "screens"
SVG_DIR = ROOT / "assets" / "svg"
DICTIONARY_PATH = ROOT.parent / "protocol" / "dictionary.yaml"
OUTPUT_PATH = ROOT / "prototype.html"

KNOWN_WIDGET_TYPES = {
    "slider", "toggle", "button", "drawer", "timer", "section",
    "readout", "label", "list", "nav", "colorpicker",
}


# --------------------------------------------------------------------------
# Loading
# --------------------------------------------------------------------------

def load_screens() -> list[dict[str, Any]]:
    """Load and lightly validate every ui/screens/*.yaml file. Never raises
    on a single bad file — warns to stderr and skips it instead, since this
    generator must keep working while real screen specs are still landing."""
    screens: list[dict[str, Any]] = []
    if not SCREENS_DIR.is_dir():
        print(f"warning: no screens directory at {SCREENS_DIR}", file=sys.stderr)
        return screens

    for path in sorted(SCREENS_DIR.glob("*.yaml")):
        try:
            data = yaml.safe_load(path.read_text(encoding="utf-8"))
        except yaml.YAMLError as exc:
            print(f"warning: skipping {path.name}: YAML parse error: {exc}", file=sys.stderr)
            continue
        if not isinstance(data, dict):
            print(f"warning: skipping {path.name}: top-level YAML is not a mapping", file=sys.stderr)
            continue
        if not data.get("screen"):
            print(f"warning: skipping {path.name}: missing required 'screen' key", file=sys.stderr)
            continue
        data.setdefault("widgets", [])
        if not isinstance(data["widgets"], list):
            print(f"warning: {path.name}: 'widgets' is not a list, ignoring it", file=sys.stderr)
            data["widgets"] = []
        data.setdefault("icons", [])
        data["_source"] = path.name
        screens.append(data)
    return screens


def load_dictionary() -> dict[str, Any] | None:
    """Optional cross-reference for field offsets/widths/ranges. Absence is
    fine — traceability annotations just fall back to the raw field/action
    names from the screen spec."""
    if not DICTIONARY_PATH.is_file():
        return None
    try:
        data = yaml.safe_load(DICTIONARY_PATH.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        print(f"warning: could not parse {DICTIONARY_PATH}: {exc}", file=sys.stderr)
        return None
    if not isinstance(data, dict):
        return None
    return data


def lookup_dictionary_field(dictionary: dict[str, Any] | None, function: str | None,
                             field: str | None) -> dict[str, Any] | None:
    if not dictionary or not function or not field:
        return None
    functions = dictionary.get("functions") or {}
    fn = functions.get(function)
    if not isinstance(fn, dict):
        return None
    for bucket in ("control_fields", "state_fields"):
        for entry in fn.get(bucket) or []:
            if isinstance(entry, dict) and entry.get("name") == field:
                return entry
    return None


# --------------------------------------------------------------------------
# Label humanization
# --------------------------------------------------------------------------

def _camel_to_words(token: str) -> str:
    token = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", " ", token)
    token = re.sub(r"(?<=[A-Z])(?=[A-Z][a-z])", " ", token)
    return token


def humanize_label(label_key: str | None, screen: str, widget_id: str | None = None) -> str:
    """Turn e.g. 'coolboxPage_coolingLevelSlider_coolingLevel_text' into
    'Cooling Level' by stripping the '<screen>Page_<widget>_...' prefix and
    trailing '_text', then splitting remaining camelCase tokens into words."""
    if not label_key:
        fallback = widget_id or screen
        return _camel_to_words(fallback).strip().title()

    tokens = label_key.split("_")
    if tokens and tokens[0] == f"{screen}Page":
        tokens = tokens[1:]
    if widget_id and tokens and tokens[0] == widget_id:
        tokens = tokens[1:]
    if tokens and tokens[0] == "pageTitle":
        tokens = tokens[1:]
    if tokens and tokens[-1] == "text":
        tokens = tokens[:-1]

    if not tokens:
        fallback = widget_id or screen
        return _camel_to_words(fallback).strip().title()

    words = " ".join(_camel_to_words(t) for t in tokens)
    return " ".join(w.capitalize() for w in words.split())


# --------------------------------------------------------------------------
# Small helpers
# --------------------------------------------------------------------------

def esc(value: Any) -> str:
    return html.escape(str(value), quote=True)


def js_str(value: Any) -> str:
    return json.dumps(str(value))


def parse_range(range_str: str | None) -> tuple[float, float, float]:
    if range_str:
        m = re.search(r"(-?\d+(?:\.\d+)?)\s*\.\.\s*(-?\d+(?:\.\d+)?)", str(range_str))
        if m:
            lo, hi = float(m.group(1)), float(m.group(2))
            is_int = lo.is_integer() and hi.is_integer()
            step = 1 if is_int else 0.1
            return (int(lo) if is_int else lo, int(hi) if is_int else hi, step)
    return (0, 100, 1)


def slugify(value: str) -> str:
    slug = re.sub(r"[^a-zA-Z0-9_-]+", "-", str(value)).strip("-")
    return slug or "x"


# --------------------------------------------------------------------------
# Icons (inline SVG, embedded — no external references)
# --------------------------------------------------------------------------

_ICON_CACHE: dict[str, str] = {}


def _clean_svg_markup(markup: str) -> str:
    markup = re.sub(r"<\?xml[^>]*\?>", "", markup)
    markup = re.sub(r"<!DOCTYPE[^>]*>", "", markup, flags=re.IGNORECASE)
    markup = re.sub(r"<!--.*?-->", "", markup, flags=re.DOTALL)
    # Drop hard-coded width/height on the root <svg> so CSS controls sizing.
    def _strip_wh(m: re.Match[str]) -> str:
        tag = m.group(0)
        tag = re.sub(r'\s(?:width|height)="[^"]*"', "", tag)
        return tag
    markup = re.sub(r"<svg\b[^>]*>", _strip_wh, markup, count=1)
    return markup.strip()


def _placeholder_icon(name: str) -> str:
    letter = esc((name[:1] or "?").upper())
    return (
        f'<svg viewBox="0 0 24 24" role="img" aria-label="{esc(name)} icon (placeholder)">'
        f'<circle cx="12" cy="12" r="10" fill="none" stroke="currentColor" stroke-width="1.4"/>'
        f'<text x="12" y="16" text-anchor="middle" font-size="10" '
        f'font-family="inherit" fill="currentColor">{letter}</text>'
        f"</svg>"
    )


def load_icon(name: str | None) -> str:
    """Return an inline <svg> markup string: the real app icon from
    ui/assets/svg/<name>.svg if present, otherwise a neutral placeholder
    glyph. Cached since the same icon can appear on several screens."""
    if not name:
        return ""
    if name in _ICON_CACHE:
        return _ICON_CACHE[name]
    path = SVG_DIR / f"{name}.svg"
    if path.is_file():
        try:
            markup = _clean_svg_markup(path.read_text(encoding="utf-8"))
        except OSError as exc:
            print(f"warning: could not read icon {path}: {exc}", file=sys.stderr)
            markup = _placeholder_icon(name)
    else:
        markup = _placeholder_icon(name)
    _ICON_CACHE[name] = markup
    return markup


def icon_span(name: str | None, css_class: str = "icon") -> str:
    if not name:
        return ""
    return f'<span class="{css_class}" title="{esc(name)}">{load_icon(name)}</span>'


# --------------------------------------------------------------------------
# Control-binding traceability annotation
# --------------------------------------------------------------------------

def format_binding(controls: dict[str, Any] | None, function: str | None,
                    dictionary: dict[str, Any] | None) -> str:
    if not controls or not isinstance(controls, dict):
        return ""
    field = controls.get("field")
    action = controls.get("action")
    if field and action:
        core = f"{field} / {action}"
    elif field:
        core = f"{field}"
    else:
        core = ", ".join(f"{k}: {v}" for k, v in controls.items())

    extra_bits = []
    info = lookup_dictionary_field(dictionary, function, field) if field else None
    if info:
        if info.get("offset") is not None:
            extra_bits.append(f"offset {info['offset']}")
        if info.get("width") is not None:
            extra_bits.append(f"width {info['width']}")
        if info.get("raw_range"):
            extra_bits.append(f"range {info['raw_range']}")
    extra = f"  ({', '.join(extra_bits)})" if extra_bits else ""
    return f'<div class="binding">&rarr; {esc(core)}{esc(extra)}</div>'


def constraint_note(widget: dict[str, Any]) -> str:
    constraints = widget.get("constraints")
    if not constraints:
        return ""
    return f'<div class="constraint-note">{esc(constraints)}</div>'


# --------------------------------------------------------------------------
# Widget rendering
# --------------------------------------------------------------------------

def render_widget(screen: str, widget: dict[str, Any], function: str | None,
                   dictionary: dict[str, Any] | None) -> str:
    wtype = widget.get("type", "label")
    wid = widget.get("id") or slugify(widget.get("label_key") or wtype)
    dom_id = f"{slugify(screen)}-{slugify(wid)}"
    label = humanize_label(widget.get("label_key"), screen, widget.get("id"))
    binding = format_binding(widget.get("controls"), function, dictionary)
    constraint = constraint_note(widget)

    if wtype not in KNOWN_WIDGET_TYPES:
        print(f"warning: {screen}.{wid}: unknown widget type '{wtype}', rendering as label",
              file=sys.stderr)
        wtype = "label"

    if wtype == "section":
        return f'<h3 class="section-heading" id="{dom_id}">{esc(label)}</h3>'

    if wtype == "label":
        return f'<p class="widget widget-label-text">{esc(label)}</p>'

    if wtype == "readout":
        value = widget.get("value", "–")
        unit = widget.get("unit", "")
        return (
            f'<div class="widget widget-readout">'
            f'<div class="readout-tile">'
            f'<span class="readout-label">{esc(label)}</span>'
            f'<span class="readout-value">{esc(value)}{esc(unit)}</span>'
            f"</div>{constraint}{binding}</div>"
        )

    if wtype == "slider":
        lo, hi, step = parse_range(widget.get("range"))
        default = widget.get("value", lo)
        unit = widget.get("unit", "")
        range_label = f"{lo}–{hi}" if widget.get("range") else ""
        return (
            f'<div class="widget widget-slider">'
            f'<div class="widget-row">'
            f'<label for="{dom_id}">{esc(label)}</label>'
            f'<span class="value-pill" id="{dom_id}-val">{esc(default)}{esc(unit)}</span>'
            f"</div>"
            f'<input type="range" class="slider" id="{dom_id}" '
            f'min="{lo}" max="{hi}" step="{step}" value="{default}" '
            f'oninput="document.getElementById({js_str(dom_id + "-val")}).textContent='
            f'this.value+{js_str(unit)}">'
            f'<div class="range-caption">{esc(range_label)}</div>'
            f"{constraint}{binding}</div>"
        )

    if wtype == "toggle":
        checked = "checked" if widget.get("value") in (True, "on", "1", 1) else ""
        return (
            f'<div class="widget widget-toggle">'
            f'<div class="widget-row">'
            f'<span class="widget-label" id="{dom_id}-label">{esc(label)}</span>'
            f'<label class="switch" aria-labelledby="{dom_id}-label">'
            f'<input type="checkbox" id="{dom_id}" {checked}>'
            f'<span class="switch-track"><span class="switch-thumb"></span></span>'
            f"</label></div>{constraint}{binding}</div>"
        )

    if wtype == "button":
        return (
            f'<div class="widget widget-button">'
            f'<button type="button" class="btn" id="{dom_id}" '
            f'onclick="this.classList.toggle(\'pressed\')">{esc(label)}</button>'
            f"{constraint}{binding}</div>"
        )

    if wtype == "nav":
        target = widget.get("target", "home")
        return (
            f'<div class="widget widget-nav">'
            f'<button type="button" class="btn btn-nav" '
            f'onclick="showScreen({js_str(target)})">{esc(label)} &rsaquo;</button>'
            f"{constraint}{binding}</div>"
        )

    if wtype == "timer":
        elements = widget.get("elements") or []
        buttons = "".join(
            f'<button type="button" class="btn btn-chip">{esc(el.get("label", "") if isinstance(el, dict) else el)}</button>'
            for el in elements
        )
        return (
            f'<div class="widget widget-timer">'
            f'<div class="timer-card">'
            f'<span class="widget-label">{esc(label)}</span>'
            f'<div class="timer-actions">{buttons}</div>'
            f"</div>{constraint}{binding}</div>"
        )

    if wtype == "colorpicker":
        elements = widget.get("elements") or ["#3fa9f5", "#f5a93f", "#7ee081", "#e0567e"]
        swatches = "".join(
            f'<button type="button" class="swatch" style="background:{esc(color)}" '
            f'aria-label="{esc(color)}" onclick="'
            f"this.parentElement.querySelectorAll('.swatch').forEach(s=>s.classList.remove('selected'));"
            f"this.classList.add('selected')\"></button>"
            for color in elements
        )
        return (
            f'<div class="widget widget-colorpicker">'
            f'<span class="widget-label">{esc(label)}</span>'
            f'<div class="swatch-row">{swatches}</div>'
            f"{constraint}{binding}</div>"
        )

    if wtype == "list":
        elements = widget.get("elements") or []
        items = "".join(
            f'<li>{esc(el.get("label", "") if isinstance(el, dict) else el)}</li>' for el in elements
        )
        return (
            f'<div class="widget widget-list">'
            f'<span class="widget-label">{esc(label)}</span>'
            f'<ul class="list-items">{items}</ul>'
            f"{constraint}{binding}</div>"
        )

    if wtype == "drawer":
        elements = widget.get("elements") or []
        body_parts = []
        for el in elements:
            if isinstance(el, dict):
                el_label = el.get("label", "")
                el_type = el.get("type", "label")
                el_binding = format_binding(el.get("controls"), function, dictionary)
                if el_type == "button":
                    body_parts.append(
                        f'<button type="button" class="btn btn-chip">{esc(el_label)}</button>{el_binding}'
                    )
                else:
                    body_parts.append(f'<p class="drawer-item">{esc(el_label)}</p>{el_binding}')
            else:
                body_parts.append(f'<p class="drawer-item">{esc(el)}</p>')
        return (
            f'<details class="widget widget-drawer">'
            f'<summary>{esc(label)}</summary>'
            f'<div class="drawer-body">{"".join(body_parts)}</div>'
            f"{constraint}{binding}</details>"
        )

    # Fallback (shouldn't be reached given the KNOWN_WIDGET_TYPES guard above)
    return f'<p class="widget widget-label-text">{esc(label)}</p>'


# --------------------------------------------------------------------------
# Screen / navigation assembly
# --------------------------------------------------------------------------

def render_screen(screen_def: dict[str, Any], dictionary: dict[str, Any] | None) -> str:
    name = screen_def["screen"]
    slug = slugify(name)
    title = humanize_label(screen_def.get("title_key"), name) or name.capitalize()
    function = screen_def.get("function")
    icons_html = "".join(icon_span(i, "icon icon-topbar") for i in (screen_def.get("icons") or []))
    widgets_html = "".join(
        render_widget(name, w, function, dictionary) for w in (screen_def.get("widgets") or [])
    )
    if not widgets_html:
        widgets_html = '<p class="empty-state">No widgets defined for this screen yet.</p>'
    notes = screen_def.get("notes")
    notes_html = f'<p class="screen-notes">{esc(notes)}</p>' if notes else ""
    source = screen_def.get("_source", "")

    return f"""
    <section class="screen" id="screen-{slug}" data-title="{esc(title)}">
      <div class="screen-topbar">
        <button type="button" class="icon-btn back-btn" onclick="showScreen('home')" aria-label="Back to home">
          <svg viewBox="0 0 24 24"><path d="M15 5l-7 7 7 7" fill="none" stroke="currentColor" stroke-width="2"
          stroke-linecap="round" stroke-linejoin="round"/></svg>
        </button>
        <h2>{esc(title)}</h2>
        <div class="topbar-icons">{icons_html}</div>
      </div>
      <div class="screen-body">
        {widgets_html}
        {notes_html}
        <p class="source-tag">{esc(source)}</p>
      </div>
    </section>"""


def render_home(screens: list[dict[str, Any]]) -> str:
    if screens:
        cards = "".join(
            f'''<button type="button" class="home-card" onclick="showScreen({js_str(s["screen"])})">
                <span class="home-card-icon">{
                    icon_span((s.get("icons") or [None])[0], "icon icon-home") or _placeholder_icon(s["screen"])
                }</span>
                <span class="home-card-title">{esc(humanize_label(s.get("title_key"), s["screen"]) or s["screen"].capitalize())}</span>
            </button>'''
            for s in screens
        )
    else:
        cards = (
            '<p class="empty-state">No screens found. Add YAML files under '
            '<code>ui/screens/</code> and regenerate.</p>'
        )
    return f"""
    <section class="screen active" id="screen-home" data-title="VW California">
      <div class="screen-topbar screen-topbar-home">
        <h2>VW California</h2>
      </div>
      <div class="screen-body">
        <div class="home-grid">{cards}</div>
      </div>
    </section>"""


def render_side_nav(screens: list[dict[str, Any]]) -> str:
    items = "".join(
        f'''<button type="button" class="navlink" data-target="{esc(s["screen"])}"
             onclick="showScreen({js_str(s["screen"])})">
             {icon_span((s.get("icons") or [None])[0], "icon icon-nav")}
             <span>{esc(humanize_label(s.get("title_key"), s["screen"]) or s["screen"].capitalize())}</span>
        </button>'''
        for s in screens
    )
    return f"""
    <nav class="side-nav" aria-label="Screens">
      <button type="button" class="navlink active" data-target="home" onclick="showScreen('home')">
        <svg class="icon" viewBox="0 0 24 24"><path d="M3 11l9-8 9 8M5 10v10h14V10" fill="none"
        stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"/></svg>
        <span>Home</span>
      </button>
      {items}
    </nav>"""


# --------------------------------------------------------------------------
# Static template (CSS + JS)
# --------------------------------------------------------------------------

CSS = """
:root {
  color-scheme: light dark;
  --bg: #eef1f5;
  --fg: #1a1d24;
  --muted: #62697a;
  --card: #ffffff;
  --card-border: #dfe3ea;
  --accent: #2f6fed;
  --accent-fg: #ffffff;
  --phone-bg: #f7f8fb;
  --phone-border: #10131a;
  --track-off: #c9ced8;
  --shadow: 0 20px 45px -20px rgba(20, 24, 33, 0.35);
  --chip-bg: #eef1f7;
  --binding-fg: #7a5cff;
  --font: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
  --mono: ui-monospace, SFMono-Regular, "SF Mono", Menlo, Consolas, monospace;
}

@media (prefers-color-scheme: dark) {
  :root:not([data-theme="light"]) {
    --bg: #14161c;
    --fg: #eef0f4;
    --muted: #9aa1b2;
    --card: #1e212a;
    --card-border: #2c3040;
    --accent: #6c93ff;
    --accent-fg: #0c0f16;
    --phone-bg: #101218;
    --phone-border: #05060a;
    --track-off: #3a3f4d;
    --shadow: 0 20px 45px -20px rgba(0, 0, 0, 0.6);
    --chip-bg: #262a35;
    --binding-fg: #b3a2ff;
  }
}

:root[data-theme="dark"] {
  --bg: #14161c;
  --fg: #eef0f4;
  --muted: #9aa1b2;
  --card: #1e212a;
  --card-border: #2c3040;
  --accent: #6c93ff;
  --accent-fg: #0c0f16;
  --phone-bg: #101218;
  --phone-border: #05060a;
  --track-off: #3a3f4d;
  --shadow: 0 20px 45px -20px rgba(0, 0, 0, 0.6);
  --chip-bg: #262a35;
  --binding-fg: #b3a2ff;
}

:root[data-theme="light"] {
  --bg: #eef1f5;
  --fg: #1a1d24;
  --muted: #62697a;
  --card: #ffffff;
  --card-border: #dfe3ea;
  --accent: #2f6fed;
  --accent-fg: #ffffff;
  --phone-bg: #f7f8fb;
  --phone-border: #10131a;
  --track-off: #c9ced8;
  --shadow: 0 20px 45px -20px rgba(20, 24, 33, 0.35);
  --chip-bg: #eef1f7;
  --binding-fg: #7a5cff;
}

* { box-sizing: border-box; }
html, body {
  margin: 0;
  padding: 0;
  background: var(--bg);
  color: var(--fg);
  font-family: var(--font);
  -webkit-font-smoothing: antialiased;
}

.page {
  min-height: 100vh;
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 20px;
  padding: 32px 16px 64px;
}

.toolbar {
  width: 100%;
  max-width: 720px;
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  flex-wrap: wrap;
}

.re-banner {
  width: 100%;
  max-width: 720px;
  margin: 0 auto 16px;
  padding: 10px 14px;
  font-size: 12px;
  line-height: 1.5;
  color: #5a5a5a;
  background: rgba(120, 120, 120, 0.08);
  border: 1px solid rgba(120, 120, 120, 0.25);
  border-radius: 8px;
}
.re-banner code { font-size: 11px; }

.toolbar h1 {
  font-size: 1.05rem;
  font-weight: 600;
  margin: 0;
  letter-spacing: -0.01em;
}

.toolbar .toolbar-controls {
  display: flex;
  align-items: center;
  gap: 14px;
  flex-wrap: wrap;
}

.toggle-label {
  display: flex;
  align-items: center;
  gap: 6px;
  font-size: 0.82rem;
  color: var(--muted);
  cursor: pointer;
  user-select: none;
}

.theme-btn {
  border: 1px solid var(--card-border);
  background: var(--card);
  color: var(--fg);
  border-radius: 999px;
  padding: 6px 14px;
  font-size: 0.82rem;
  font-family: inherit;
  cursor: pointer;
  display: flex;
  align-items: center;
  gap: 6px;
}
.theme-btn:hover { border-color: var(--accent); }

.app { position: relative; }

.phone {
  width: min(90vw, 400px);
  background: var(--phone-border);
  border-radius: 46px;
  padding: 14px;
  box-shadow: var(--shadow);
}

.phone-screen {
  position: relative;
  background: var(--phone-bg);
  border-radius: 32px;
  overflow: hidden;
  height: min(80vh, 820px);
  display: flex;
  flex-direction: column;
}

.status-bar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 10px 22px 4px;
  font-size: 0.78rem;
  font-weight: 600;
  color: var(--fg);
  flex-shrink: 0;
}
.status-bar .status-icons { display: flex; gap: 6px; align-items: center; color: var(--fg); }
.status-bar svg { width: 18px; height: 12px; }

.menu-btn {
  border: none;
  background: transparent;
  color: var(--fg);
  cursor: pointer;
  padding: 4px;
  display: flex;
}
.menu-btn svg { width: 22px; height: 22px; }

.phone-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 2px 14px 8px;
  flex-shrink: 0;
}

.phone-body {
  flex: 1;
  overflow-y: auto;
  padding: 4px 18px 24px;
  scrollbar-width: thin;
}

.screen { display: none; }
.screen.active { display: block; }

.screen-topbar {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 6px 0 14px;
  position: sticky;
  top: 0;
  background: var(--phone-bg);
}
.screen-topbar h2 {
  flex: 1;
  margin: 0;
  font-size: 1.1rem;
  font-weight: 700;
  letter-spacing: -0.01em;
}
.screen-topbar-home h2 { font-size: 1.3rem; }
.topbar-icons { display: flex; gap: 8px; color: var(--muted); }
.topbar-icons .icon svg { width: 18px; height: 18px; }

.icon-btn {
  border: none;
  background: var(--chip-bg);
  color: var(--fg);
  width: 32px;
  height: 32px;
  border-radius: 50%;
  display: flex;
  align-items: center;
  justify-content: center;
  cursor: pointer;
}
.icon-btn svg { width: 16px; height: 16px; }

.widget {
  background: var(--card);
  border: 1px solid var(--card-border);
  border-radius: 16px;
  padding: 14px 16px;
  margin-bottom: 12px;
}
.widget-label-text, .screen-notes {
  color: var(--muted);
  font-size: 0.88rem;
  line-height: 1.4;
  margin: 4px 0 14px;
  background: none;
  border: none;
  padding: 0;
}
.section-heading {
  margin: 22px 0 8px;
  font-size: 0.78rem;
  text-transform: uppercase;
  letter-spacing: 0.08em;
  color: var(--muted);
  font-weight: 700;
}
.section-heading:first-child { margin-top: 4px; }

.widget-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 10px;
}
.widget-label { font-size: 0.95rem; font-weight: 500; }

.value-pill {
  background: var(--chip-bg);
  color: var(--fg);
  font-variant-numeric: tabular-nums;
  font-size: 0.85rem;
  font-weight: 600;
  padding: 3px 10px;
  border-radius: 999px;
}

input[type="range"].slider {
  width: 100%;
  margin-top: 12px;
  accent-color: var(--accent);
}
.range-caption {
  margin-top: 4px;
  font-size: 0.72rem;
  color: var(--muted);
  font-family: var(--mono);
}

.switch { position: relative; display: inline-block; width: 46px; height: 27px; flex-shrink: 0; }
.switch input { position: absolute; opacity: 0; width: 100%; height: 100%; margin: 0; cursor: pointer; z-index: 1; }
.switch-track {
  position: absolute; inset: 0; background: var(--track-off);
  border-radius: 999px; transition: background 0.15s ease;
}
.switch-thumb {
  position: absolute; top: 2px; left: 2px; width: 23px; height: 23px;
  background: #fff; border-radius: 50%; box-shadow: 0 1px 3px rgba(0,0,0,0.3);
  transition: transform 0.15s ease;
}
.switch input:checked + .switch-track { background: var(--accent); }
.switch input:checked + .switch-track .switch-thumb { transform: translateX(19px); }

.btn {
  font-family: inherit;
  border: none;
  background: var(--accent);
  color: var(--accent-fg);
  padding: 10px 16px;
  border-radius: 12px;
  font-size: 0.9rem;
  font-weight: 600;
  cursor: pointer;
  width: 100%;
}
.btn:hover { filter: brightness(1.06); }
.btn.pressed { filter: brightness(0.85); }
.btn-nav { background: var(--chip-bg); color: var(--fg); text-align: left; }
.btn-chip {
  width: auto;
  background: var(--chip-bg);
  color: var(--fg);
  padding: 8px 14px;
  font-size: 0.82rem;
}

.readout-tile { display: flex; flex-direction: column; gap: 4px; }
.readout-label { font-size: 0.82rem; color: var(--muted); }
.readout-value { font-size: 1.8rem; font-weight: 700; font-variant-numeric: tabular-nums; }

.timer-card { display: flex; flex-direction: column; gap: 10px; }
.timer-actions { display: flex; gap: 8px; flex-wrap: wrap; }

.swatch-row { display: flex; gap: 10px; margin-top: 10px; flex-wrap: wrap; }
.swatch {
  width: 32px; height: 32px; border-radius: 50%; border: 2px solid transparent;
  cursor: pointer; padding: 0;
}
.swatch.selected { border-color: var(--fg); }

.list-items { margin: 10px 0 0; padding-left: 20px; color: var(--fg); font-size: 0.88rem; }
.list-items li { margin-bottom: 4px; }

.widget-drawer summary {
  cursor: pointer; font-weight: 600; font-size: 0.95rem;
  list-style: none;
}
.widget-drawer summary::-webkit-details-marker { display: none; }
.widget-drawer summary::before { content: "\\25B8"; display: inline-block; margin-right: 8px; transition: transform 0.15s ease; }
.widget-drawer[open] summary::before { transform: rotate(90deg); }
.drawer-body { margin-top: 12px; display: flex; flex-direction: column; gap: 8px; }
.drawer-item { margin: 0; font-size: 0.85rem; color: var(--muted); }

.binding {
  margin-top: 10px;
  font-family: var(--mono);
  font-size: 0.72rem;
  color: var(--binding-fg);
  display: none;
}
.app.show-bindings .binding { display: block; }
.constraint-note {
  margin-top: 8px;
  font-size: 0.76rem;
  color: var(--muted);
  font-style: italic;
}

.empty-state { color: var(--muted); font-size: 0.9rem; text-align: center; padding: 30px 10px; }
.source-tag { display: none; }
.app.show-bindings .source-tag {
  display: block; margin-top: 20px; font-family: var(--mono); font-size: 0.68rem; color: var(--muted); opacity: 0.7;
}

.home-grid {
  display: grid;
  grid-template-columns: repeat(2, 1fr);
  gap: 14px;
  margin-top: 6px;
}
.home-card {
  background: var(--card);
  border: 1px solid var(--card-border);
  border-radius: 18px;
  padding: 18px 12px;
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 10px;
  cursor: pointer;
  font-family: inherit;
  color: var(--fg);
}
.home-card:hover { border-color: var(--accent); }
.home-card-icon { width: 40px; height: 40px; color: var(--accent); }
.home-card-icon svg { width: 100%; height: 100%; }
.home-card-title { font-size: 0.85rem; font-weight: 600; text-align: center; }

.side-nav {
  position: fixed;
  top: 0; left: 0; bottom: 0;
  width: 240px;
  background: var(--card);
  border-right: 1px solid var(--card-border);
  padding: 24px 14px;
  display: flex;
  flex-direction: column;
  gap: 4px;
  transform: translateX(-100%);
  transition: transform 0.2s ease;
  z-index: 20;
  overflow-y: auto;
}
.app.nav-open .side-nav { transform: translateX(0); }
.side-nav .navlink {
  display: flex;
  align-items: center;
  gap: 10px;
  border: none;
  background: transparent;
  color: var(--fg);
  font-family: inherit;
  font-size: 0.9rem;
  padding: 10px 12px;
  border-radius: 10px;
  cursor: pointer;
  text-align: left;
}
.side-nav .navlink .icon svg { width: 18px; height: 18px; }
.side-nav .navlink:hover { background: var(--chip-bg); }
.side-nav .navlink.active { background: var(--accent); color: var(--accent-fg); }

.nav-backdrop {
  display: none;
  position: fixed; inset: 0; background: rgba(10, 12, 18, 0.45); z-index: 10;
}
.app.nav-open .nav-backdrop { display: block; }

.icon svg { width: 20px; height: 20px; display: block; fill: none; stroke: currentColor; }
.icon-placeholder svg { fill: currentColor; }

@media (max-width: 480px) {
  .phone { width: 94vw; }
}
"""

JS = """
function showScreen(id) {
  document.querySelectorAll('.screen').forEach(function (el) {
    el.classList.toggle('active', el.id === 'screen-' + id);
  });
  document.querySelectorAll('.navlink').forEach(function (el) {
    el.classList.toggle('active', el.dataset.target === id);
  });
  var body = document.querySelector('.phone-body');
  if (body) { body.scrollTop = 0; }
  closeSideNav();
}

function toggleSideNav() {
  document.querySelector('.app').classList.toggle('nav-open');
}
function closeSideNav() {
  document.querySelector('.app').classList.remove('nav-open');
}

function cycleTheme() {
  var root = document.documentElement;
  var order = ['auto', 'dark', 'light'];
  var cur = root.getAttribute('data-theme') || 'auto';
  var next = order[(order.indexOf(cur) + 1) % order.length];
  if (next === 'auto') { root.removeAttribute('data-theme'); }
  else { root.setAttribute('data-theme', next); }
  var btn = document.getElementById('theme-btn-label');
  if (btn) { btn.textContent = next.charAt(0).toUpperCase() + next.slice(1); }
}

function toggleBindings(checked) {
  document.querySelector('.app').classList.toggle('show-bindings', checked);
}
"""


def build_html(screens: list[dict[str, Any]], dictionary: dict[str, Any] | None) -> str:
    home_html = render_home(screens)
    screens_html = "".join(render_screen(s, dictionary) for s in screens)
    side_nav_html = render_side_nav(screens)

    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>calictl — camper hardware-control interface</title>
<style>{CSS}</style>
</head>
<body>
<div class="page">
  <div class="toolbar">
    <h1>calictl &mdash; camper hardware-control interface</h1>
    <div class="toolbar-controls">
      <label class="toggle-label">
        <input type="checkbox" onchange="toggleBindings(this.checked)">
        Show control bindings
      </label>
      <button type="button" class="theme-btn" onclick="cycleTheme()">
        <span>Theme:</span> <span id="theme-btn-label">Auto</span>
      </button>
    </div>
  </div>

  <p class="re-banner">Reverse-engineering spec preview &mdash; an independent, hardware-function-driven
  interface derived from the camper's BLE control surface (see <code>docs/UI-DESIGN-RATIONALE.md</code>).
  This is developer documentation, not the control app: the actual UI is served by the daemon from
  <code>calictl/webui</code>. Technical notes cite the vendor app by factual reference only (symbol /
  key names / file:line), never reproduced text or artwork.</p>

  <div class="app">
    <div class="nav-backdrop" onclick="closeSideNav()"></div>
    {side_nav_html}
    <div class="phone">
      <div class="phone-screen">
        <div class="status-bar">
          <span>9:41</span>
          <span class="status-icons">
            <svg viewBox="0 0 20 12"><rect x="0" y="7" width="3" height="5"/><rect x="5" y="4" width="3" height="8"/>
            <rect x="10" y="1" width="3" height="11"/></svg>
            <svg viewBox="0 0 24 12"><rect x="0" y="1" width="20" height="10" rx="2" fill="none" stroke="currentColor" stroke-width="1.2"/>
            <rect x="2" y="3" width="14" height="6" fill="currentColor"/><rect x="21" y="4" width="2" height="4" fill="currentColor"/></svg>
          </span>
        </div>
        <div class="phone-header">
          <button type="button" class="menu-btn" onclick="toggleSideNav()" aria-label="Open menu">
            <svg viewBox="0 0 24 24"><path d="M4 6h16M4 12h16M4 18h16" fill="none" stroke="currentColor"
            stroke-width="2" stroke-linecap="round"/></svg>
          </button>
        </div>
        <div class="phone-body">
          {home_html}
          {screens_html}
        </div>
      </div>
    </div>
  </div>
</div>
<script>{JS}</script>
</body>
</html>
"""


def main() -> int:
    screens = load_screens()
    dictionary = load_dictionary()
    output = build_html(screens, dictionary)
    OUTPUT_PATH.write_text(output, encoding="utf-8")
    print(f"wrote {OUTPUT_PATH} ({len(output):,} bytes) from {len(screens)} screen(s)")
    if dictionary is None:
        print(f"note: {DICTIONARY_PATH} not found — control-binding annotations will skip dictionary cross-reference")
    return 0


if __name__ == "__main__":
    sys.exit(main())

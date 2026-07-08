"""Compile ui/screens/*.yaml + ui/labels.yaml -> calictl/webui/screens.json.

Tooling (PyYAML allowed). Committed output carries only neutral labels; the optional local
overlay (--strings a pre-extracted APK .cvr JSON map of label_key->real text) is never
committed. See docs/superpowers/specs/2026-07-08-app-replica-webserver-design.md.
"""
from __future__ import annotations
import argparse
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCREENS_DIR = ROOT / "ui" / "screens"
LABELS_FILE = ROOT / "ui" / "labels.yaml"
OUT_FILE = ROOT / "calictl" / "webui" / "screens.json"

# Vehicle-tab control-flow scope (dashboard + per-feature control screens).
CONTROL_SCREENS = ["home", "coolbox", "camping-mode", "lighting", "air-heater",
                   "roof", "water", "energy", "vehicle-info"]


def humanize(label_key):
    """Mechanical neutral label from a compose resource key (no VW text)."""
    if not label_key:
        return None
    s = re.sub(r"^[A-Za-z]+Page_", "", label_key)
    s = re.sub(r"_(text|button|title)$", "", s)
    seg = s.split("_")[-1]
    seg = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", " ", seg)   # camelCase -> spaced
    seg = seg.replace("_", " ").strip().lower()
    return seg[:1].upper() + seg[1:] if seg else None


def resolve_label(label_key, labels, strings=None):
    """Local overlay (real VW text) > committed neutral label > humanized fallback."""
    if not label_key:
        return None
    if strings and label_key in strings:
        return strings[label_key]
    if label_key in labels:
        return labels[label_key]
    return humanize(label_key)


def build(strings=None):
    import yaml   # lazy: tooling only
    labels = yaml.safe_load(LABELS_FILE.read_text()) or {}
    screens = {}
    for name in CONTROL_SCREENS:
        spec = yaml.safe_load((SCREENS_DIR / (name + ".yaml")).read_text())
        widgets = [{
            "id": w.get("id"), "type": w.get("type"),
            "label": resolve_label(w.get("label_key"), labels, strings),
            "range": w.get("range"), "controls": w.get("controls"),
        } for w in (spec.get("widgets") or [])]
        screens[name] = {
            "screen": spec.get("screen"), "function": spec.get("function"),
            "title": resolve_label(spec.get("title_key"), labels, strings),
            "widgets": widgets,
        }
    return {"screens": screens, "order": CONTROL_SCREENS}


def main(argv=None):
    ap = argparse.ArgumentParser(description="compile the web UI screen data")
    ap.add_argument("--strings", help="local APK .cvr strings JSON (label_key->text); never committed")
    ap.add_argument("--out", default=str(OUT_FILE))
    args = ap.parse_args(argv)
    strings = json.loads(Path(args.strings).read_text()) if args.strings else None
    data = build(strings)
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n")
    print("wrote %s (%d screens)" % (out, len(data["screens"])))


if __name__ == "__main__":
    main()

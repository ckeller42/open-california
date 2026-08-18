#!/usr/bin/env python3
"""PostToolUse hook — keep the Enigma mapping current after decompile / call-stack analysis.

The private decompile repo's ``mapping.enigma`` is our durable, human-owned deobfuscation layer
(meaningful class/method names + doc-comments that survive a re-decompile). It only stays useful if
it's updated whenever we newly understand a piece of the obfuscated app. This hook watches Bash
commands that READ the decompiled sources (or grep/trace them) and injects a reminder to record what
was learned into ``mapping.enigma`` — the standing "mapping grows monotonically" rule.

Reads the PostToolUse event JSON on stdin; prints additionalContext when it fires.
"""
import sys
import json

try:
    data = json.load(sys.stdin)
except Exception:
    sys.exit(0)

# Only Bash commands carry the decompile access; look at the command text.
cmd = (data.get("tool_input") or {}).get("command", "") or ""
low = cmd.lower()

# Signals that this command was a decompile read / call-stack trace, but NOT already an edit of the
# mapping itself (don't nag while you're doing the update).
reads_decompile = (
    ("californiaontour-decompile" in low or "decompile/sources" in low or "/sources/" in low
     or "baksmali" in low or "jadx" in low or "vineflower" in low)
    and any(k in low for k in ("cat ", "grep", "sed ", "awk", "less ", "head", "tail", "rg ", ".java", ".smali"))
)
edits_mapping = "mapping.enigma" in low and any(k in low for k in (">>", "commit", "append", "> mapping", "sed -i", "write"))

if reads_decompile and not edits_mapping:
    msg = ("You just read/traced the decompiled app. If you understood any class or method not yet in "
           "the Enigma mapping — or found an existing entry wrong/thin — UPDATE it now: append/enrich "
           "the class + method names and a doc-comment (with class.java:line cites) in "
           "~/src/californiaontour-decompile/mapping.enigma on buspi, then commit + push to "
           "ckeller42/californiaontour-re. Standing rule: the mapping GROWS MONOTONICALLY — never let a "
           "fresh understanding go unrecorded. (See the decompile-app skill.)")
    print(json.dumps({"hookSpecificOutput": {"hookEventName": "PostToolUse",
                                             "additionalContext": msg}}))

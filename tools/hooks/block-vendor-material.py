#!/usr/bin/env python3
"""PreToolUse hook — refuse to WRITE vendor/VW material or vehicle PII into the tree in the first
place, so the mistake is caught at the keystroke instead of in the `no-vendor-material` CI job after
it's already committed and pushed. Mirrors that CI job's patterns (.github/workflows/ci.yml):
never a decompiled source, VW manual, APK/capture binary, un-gitignored icon, or *.env into a
tracked path, and never the real vehicle MAC (OUI 20:81:9A) or a VIN into file content.

CLAUDE.md hard rule: "Never commit the APK, decompiled sources (decompile/), VW manuals (manuals/),
or secrets/tokens (*.env)". This is the local enforcement of that rule.

Reads the PreToolUse event JSON on stdin. Silent (exit 0) when the write is clean; on a match it
exits 2 with the reason on stderr, which blocks the tool call and tells the model why.
"""
import json
import re
import sys

try:
    data = json.load(sys.stdin)
except Exception:
    sys.exit(0)  # never break the tool on a malformed event

ti = data.get("tool_input") or {}
path = (ti.get("file_path") or "").replace("\\", "/")
# The text being written (Write=content, Edit=new_string) — scanned for MAC/VIN leakage.
text = (ti.get("content") or "") + "\n" + (ti.get("new_string") or "")

# Path patterns that must never become tracked files (verbatim from the CI job).
PATH_BAD = re.compile(
    r"\.(apk|xapk|dex|jar|aar|aab|cvr|pcap|pcapng|pklg|btsnoop|env)$"
    r"|(^|/)decompile/|(^|/)manuals/|ui/assets/svg/",
    re.IGNORECASE,
)
# Vehicle PII in the content: the unit's real MAC (OUI + any suffix) or a VIN (17-char token on a
# line that also names WV2ZZZ/VIN — same two-signal test the CI job and pre-commit use).
MAC = re.compile(r"20:81:9a(:[0-9a-f]{2}){3}", re.IGNORECASE)
VIN_TOKEN = re.compile(r"\b[A-HJ-NPR-Z0-9]{17}\b")
VIN_CONTEXT = re.compile(r"WV2ZZZ|VIN", re.IGNORECASE)


def block(reason: str) -> None:
    sys.stderr.write("Blocked by block-vendor-material hook: " + reason + "\n")
    sys.exit(2)


if path and PATH_BAD.search(path):
    block(
        "%s is a vendor/binary/secret path CLAUDE.md forbids committing (APK, decompile/, manuals/, "
        "captures, un-gitignored icons, or *.env). Keep it local/gitignored; cite VW material, never "
        "vendor it." % path
    )

if MAC.search(text):
    block("the real vehicle BLE MAC (OUI 20:81:9A) — use the AA:BB:CC:DD:EE:FF placeholder or an env var.")

if VIN_TOKEN.search(text) and VIN_CONTEXT.search(text):
    block("a VIN appears in the content — VINs are PII and must never be written into a tracked file.")

sys.exit(0)

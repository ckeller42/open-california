#!/usr/bin/env python3
"""Tiny adb UI driver for the emulator: dump the view tree, tap by text, screenshot.

  adbui.py dump                 -> prints visible texts (deduped, in tree order)
  adbui.py tap "<regex>"        -> taps the centre of the first node whose text/desc matches
  adbui.py shot <name>          -> screenshot to shots/<name>.png (scratchpad) + prints texts
  adbui.py tree                 -> prints (text|desc, class, bounds, clickable) per node
"""
import os
import re
import subprocess
import sys
import xml.etree.ElementTree as ET

ADB = os.environ.get("ADB") or (
    os.path.join(os.environ["ANDROID_SDK_ROOT"], "platform-tools", "adb")
    if os.environ.get("ANDROID_SDK_ROOT") else "adb")
SHOTS = os.environ.get("APPLAB_SHOTS", os.path.join(os.getcwd(), "applab-shots"))   # never inside the repo


def sh(*args, binary=False):
    r = subprocess.run([ADB, *args], capture_output=True, check=False)
    return r.stdout if binary else r.stdout.decode(errors="replace")


def tree():
    sh("shell", "uiautomator", "dump", "/sdcard/ui.xml")
    xml = sh("shell", "cat", "/sdcard/ui.xml")
    xml = xml[xml.find("<"):]
    nodes = []
    for n in ET.fromstring(xml).iter("node"):
        b = [int(x) for x in re.findall(r"\d+", n.get("bounds", ""))]
        nodes.append({
            "text": n.get("text", ""), "desc": n.get("content-desc", ""),
            "cls": n.get("class", "").split(".")[-1], "bounds": b,
            "clickable": n.get("clickable") == "true", "enabled": n.get("enabled") == "true",
            "checked": n.get("checked"), "checkable": n.get("checkable") == "true",
        })
    return nodes


def texts(nodes):
    out = []
    for n in nodes:
        for t in (n["text"], n["desc"]):
            if len(t) >= 2 and t not in out:
                out.append(t)
    return out


def tap(pattern):
    rx = re.compile(pattern, re.I)
    for n in tree():
        if rx.search(n["text"]) or rx.search(n["desc"]):
            x1, y1, x2, y2 = n["bounds"]
            sh("shell", "input", "tap", str((x1 + x2) // 2), str((y1 + y2) // 2))
            return f"tapped {n['text'] or n['desc']!r} @ {(x1 + x2) // 2},{(y1 + y2) // 2}"
    return f"NOT FOUND: {pattern}"


def shot(name):
    os.makedirs(SHOTS, exist_ok=True)
    png = sh("exec-out", "screencap", "-p", binary=True)
    path = os.path.join(SHOTS, name + ".png")
    with open(path, "wb") as f:
        f.write(png)
    return path


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "dump"
    if cmd == "dump":
        print(" | ".join(texts(tree())))
    elif cmd == "tap":
        print(tap(sys.argv[2]))
    elif cmd == "shot":
        p = shot(sys.argv[2])
        print(p)
        print(" | ".join(texts(tree())))
    elif cmd == "tree":
        for n in tree():
            if n["text"] or n["desc"] or n["clickable"]:
                print(f"{(n['text'] or n['desc'])[:60]!r:64} {n['cls']:18} {n['bounds']} "
                      f"{'click' if n['clickable'] else ''} {'chk=' + str(n['checked']) if n['checkable'] else ''}")

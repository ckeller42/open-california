"""Build the public remote app: ``calictl/webui`` as a static site for ``https://open-california.github.io/``.

The remote app (``window.OC_REMOTE``) talks only to The Things Stack and never to the owner's router (see
``docs/howto-lora-remote-setup.md``). This script writes ``_build/remote/``: the web UI files (the remote-only
scripts included, which the satellite's bundle never carries), ``vendor/``, and its OWN ``index.html``,
``manifest.webmanifest`` and ``sw.js`` (the local app's three are untouched). The site is served from the root
of its origin, so ``/sw.js`` has scope ``/`` and the page's absolute paths stay valid.

``python3 -m tools.build_remote --out _build/remote`` writes the site. ``--check`` writes nothing: it builds
into a temporary directory and exits 1 when ``--out`` does not hold exactly those files and bytes (missing,
extra or different), so CI can detect a stale or hand-edited build. The build is deterministic.

The page's Content-Security-Policy meta allows scripts only from the site itself, so there is no inline
script: the remote flag is the file ``remote-flag.js``.

"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
WEBUI = ROOT / "calictl" / "webui"

#: The policy of the remote page. ``connect-src`` names the only hosts the page may call; ``worker-src blob:``
#: is the QR scanner's worker.
CSP = (
    "default-src 'self'; connect-src 'self' https://*.thethings.network https://*.thethings.industries; "
    "img-src 'self' data: blob:; worker-src 'self' blob:; script-src 'self'"
)
CACHE = "oc-remote-shell-v1"
#: Remote-only scripts, in load order (the order of tests/e2e/test_remote_app.py), inserted before app.js.
REMOTE_SCRIPTS = (
    "strings.remote.de.js",
    "vendor/qr-scanner/qr-scanner.umd.min.js",
    "lora_setup.js",
    "ttn.js",
    "lora_decode.js",
    "remote.js",
)
#: Files of calictl/webui the site does not serve: its own three (rewritten below) and the editor/icon sources.
SKIP = {"index.html", "manifest.webmanifest", "sw.js", "jsconfig.json", "icon-master.png"}


def _replace_once(text: str, old: str, new: str) -> str:
    if text.count(old) != 1:
        raise SystemExit("build_remote: expected exactly one %r in the source file" % old)
    return text.replace(old, new)


def build(out: Path) -> None:
    """Write the site into ``out`` (emptied first)."""
    if out.exists():
        shutil.rmtree(out)
    out.mkdir(parents=True)
    for src in sorted(WEBUI.rglob("*")):
        rel = src.relative_to(WEBUI)
        if src.is_file() and rel.parts[0] not in SKIP and "__pycache__" not in rel.parts:
            (out / rel).parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(src, out / rel)
    (out / "remote-flag.js").write_text("window.OC_REMOTE = true;\n", encoding="utf-8")
    (out / ".nojekyll").write_text("", encoding="utf-8")

    html = (WEBUI / "index.html").read_text(encoding="utf-8")
    html = _replace_once(
        html,
        '<meta charset="utf-8">\n',
        '<meta charset="utf-8">\n<meta http-equiv="Content-Security-Policy" content="%s">\n' % CSP,
    )
    tag = '<script src="/%s"></script>\n'
    html = _replace_once(html, tag % "strings.de.js", tag % "remote-flag.js" + tag % "strings.de.js")
    html = _replace_once(
        html, '<script src="/app.js">', "".join(tag % n for n in REMOTE_SCRIPTS) + '<script src="/app.js">'
    )
    (out / "index.html").write_text(html, encoding="utf-8")

    manifest = json.loads((WEBUI / "manifest.webmanifest").read_text(encoding="utf-8"))
    manifest.update(
        {
            "name": "Open California remote",
            "short_name": "OC remote",
            "description": "Open California for a van with LoRa: reads the van from The Things Network",
            "id": "/",
            "start_url": "/",
        }
    )
    (out / "manifest.webmanifest").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")

    # The local service worker already caches only same-origin GET shell files and returns (does not
    # intercept) /api/* and every cross-origin request, i.e. all TTS calls: only its cache name differs.
    sw = (WEBUI / "sw.js").read_text(encoding="utf-8")
    (out / "sw.js").write_text(
        _replace_once(sw, 'const CACHE = "oc-shell-v1";', 'const CACHE = "%s";' % CACHE), encoding="utf-8"
    )


def _files(root: Path) -> dict[str, bytes]:
    return {str(p.relative_to(root)): p.read_bytes() for p in sorted(root.rglob("*")) if p.is_file()}


def check(out: Path) -> list[str]:
    """The differences between ``out`` and a fresh build (empty: identical)."""
    with tempfile.TemporaryDirectory() as tmp:
        build(Path(tmp) / "site")
        want = _files(Path(tmp) / "site")
    have = _files(out) if out.is_dir() else {}
    return (
        ["missing: " + n for n in sorted(want.keys() - have.keys())]
        + ["extra: " + n for n in sorted(have.keys() - want.keys())]
        + ["differs: " + n for n in sorted(n for n in want.keys() & have.keys() if want[n] != have[n])]
    )


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--out", type=Path, default=ROOT / "_build" / "remote")
    ap.add_argument(
        "--check", action="store_true", help="write nothing; exit 1 when --out differs from a fresh build"
    )
    a = ap.parse_args(argv)
    if a.check:
        diff = check(a.out)
        print("\n".join(diff) or "remote build is up to date")
        return 1 if diff else 0
    build(a.out)
    print("remote app written to %s" % a.out)
    return 0


if __name__ == "__main__":
    sys.exit(main())

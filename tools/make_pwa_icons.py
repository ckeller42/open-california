"""Generate open California's PWA / home-screen / favicon icons from the master tile.

The master art (``calictl/webui/icon-master.png``) is a square gradient tile: a side-view VW
California camper with an open pop-top + surfboard, a California silhouette on the door, and an
"OPEN CALIFORNIA" wordmark. This emits every size the manifest + index.html reference:

    python -m tools.make_pwa_icons                     # from the existing master
    python -m tools.make_pwa_icons --from-source S.png # re-crop the master from a spec-sheet first

Outputs (in calictl/webui/):
  icon-192.png, icon-512.png, apple-touch-icon.png        purpose "any"
  maskable-icon-192.png, maskable-icon-512.png            purpose "maskable" (content in safe zone)
  favicon-16x16.png, favicon-32x32.png, favicon.ico       browser tab

And the iPhone app's icon: ios/App/Assets.xcassets/AppIcon.appiconset/AppIcon.png (1024, opaque).
"""

from __future__ import annotations

import os

from PIL import Image, ImageChops

WEBUI = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "calictl", "webui")
MASTER = os.path.join(WEBUI, "icon-master.png")
ANY_SIZES = {"icon-192.png": 192, "icon-512.png": 512, "apple-touch-icon.png": 180}
MASKABLE_SIZES = {"maskable-icon-192.png": 192, "maskable-icon-512.png": 512}
FAVICON_PNG = {"favicon-16x16.png": 16, "favicon-32x32.png": 32}
IOS_ICONSET = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "ios",
    "App",
    "Assets.xcassets",
    "AppIcon.appiconset",
)


def master_from_source(source_path: str, crop=None, out_path: str = MASTER) -> None:
    """Crop the coloured tile out of a source render (``crop`` = explicit (l,t,r,b), else the
    largest non-background bbox) and fill any flat-background corners with the tile's own vertical
    gradient, so the result is a clean full-bleed square. Idempotent."""
    im = Image.open(source_path).convert("RGB")
    bg = im.getpixel((5, 5))
    if crop is None:
        diff = ImageChops.difference(im, Image.new("RGB", im.size, bg)).convert("L")
        crop = diff.point(lambda p: 255 if p > 30 else 0).getbbox()
    im = im.crop(crop)
    w, h = im.size
    px = im.load()

    def is_bg(c):
        return all(abs(c[i] - bg[i]) < 26 for i in range(3))

    for y in range(h):  # per-row gradient colour = median of the non-bg, non-white pixels
        grad = [px[x, y] for x in range(w) if not is_bg(px[x, y]) and not all(v > 235 for v in px[x, y])]
        if not grad:
            continue
        grad.sort(key=lambda c: sum(c))
        fill = grad[len(grad) // 2]
        for x in range(w):
            if is_bg(px[x, y]):
                px[x, y] = fill
    s = max(w, h)
    sq = Image.new("RGB", (s, s), im.getpixel((w // 2, 2)))
    sq.paste(im, ((s - w) // 2, (s - h) // 2))
    sq.save(out_path)


def _gradient_bg(master: Image.Image, size: int) -> Image.Image:
    """A full-bleed vertical gradient matching the master's own gradient (sampled from its left
    edge column, which is background, never the van), so a shrunk master seats seamlessly."""
    m = master.resize((size, size), Image.LANCZOS)
    bg = Image.new("RGB", (size, size))
    bpx, mpx = bg.load(), m.load()
    for y in range(size):
        col = mpx[2, y]  # left edge = pure gradient
        for x in range(size):
            bpx[x, y] = col
    return bg


def _maskable(master: Image.Image, size: int, scale: float = 0.82) -> Image.Image:
    """Content inside the central safe zone: the master's gradient continues to the edges while
    the van + wordmark are inset to ~82 %, so a circular/rounded mask never clips them."""
    bg = _gradient_bg(master, size)
    inner = int(size * scale)
    bg.paste(master.resize((inner, inner), Image.LANCZOS), ((size - inner) // 2, (size - inner) // 2))
    return bg


def ios_corner_margin(master: Image.Image) -> int:
    """How far to crop each edge so that no transparent pixel of the master's rounded corners is left.

    :param master: the RGBA master tile.
    :returns: the margin in pixels (the smallest one whose cropped square is fully opaque).
    """
    alpha = master.convert("RGBA").getchannel("A")
    w, h = alpha.size
    for m in range(w // 2):
        if alpha.crop((m, m, w - m, h - m)).getextrema()[0] == 255:
            return m
    raise ValueError("the master has no opaque core")


def ios_icon(master: Image.Image) -> Image.Image:
    """The iPhone app icon: the master cropped past its rounded corners, 1024 x 1024, no alpha (iOS
    rounds the corners itself and refuses an icon with transparency)."""
    rgba = master.convert("RGBA")
    m = ios_corner_margin(rgba)
    square = rgba.crop((m, m, rgba.width - m, rgba.height - m)).resize((1024, 1024), Image.LANCZOS)
    return square.convert("RGB")


def main(argv=None):
    import argparse

    ap = argparse.ArgumentParser(description="build PWA / favicon icons from the master tile")
    ap.add_argument("--from-source", help="re-crop icon-master.png from this spec-sheet render")
    ap.add_argument("--crop", help="explicit crop box l,t,r,b for --from-source")
    args = ap.parse_args(argv)
    if args.from_source:
        crop = tuple(int(v) for v in args.crop.split(",")) if args.crop else None
        master_from_source(args.from_source, crop=crop)
        print("wrote", MASTER)
    master = Image.open(MASTER).convert("RGB")
    for name, sz in ANY_SIZES.items():
        master.resize((sz, sz), Image.LANCZOS).save(os.path.join(WEBUI, name))
        print("wrote", name)
    for name, sz in MASKABLE_SIZES.items():
        _maskable(master, sz).save(os.path.join(WEBUI, name))
        print("wrote", name)
    for name, sz in FAVICON_PNG.items():
        master.resize((sz, sz), Image.LANCZOS).save(os.path.join(WEBUI, name))
        print("wrote", name)
    master.save(os.path.join(WEBUI, "favicon.ico"), sizes=[(16, 16), (32, 32), (48, 48)])
    print("wrote favicon.ico")
    os.makedirs(IOS_ICONSET, exist_ok=True)
    ios_icon(Image.open(MASTER)).save(os.path.join(IOS_ICONSET, "AppIcon.png"))
    print("wrote", os.path.relpath(os.path.join(IOS_ICONSET, "AppIcon.png")))


if __name__ == "__main__":
    main()

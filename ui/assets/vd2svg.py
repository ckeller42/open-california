#!/usr/bin/env python3
"""
vd2svg.py -- Convert Android vector-drawable XML files to SVG.

Android vector-drawable pathData is already SVG-path-compatible, so this is
mostly a tag/attribute remap:

    <vector android:viewportWidth=W android:viewportHeight=H>       -> <svg viewBox="0 0 W H">
    <path android:pathData=D android:fillColor=C android:fillType=T> -> <path d=D fill=C fill-rule=T'>
    <group> ... </group>                                             -> <g> ... </g>
    <clip-path android:pathData=D/>                                  -> <clipPath><path d=D/></clipPath>
    <aapt:attr name="android:fillColor"><gradient .../></aapt:attr>  -> <linearGradient>/<radialGradient> in <defs>, referenced via fill="url(#...)"

Stdlib only (xml.etree.ElementTree). Best-effort: groups with rotate/
translate/scale transforms are not expected in this icon set (verified none
present) but if encountered they are mapped to an SVG transform attribute.

Security note: xml.etree.ElementTree is not hardened against XXE / entity-
expansion attacks. That's acceptable here: inputs are drawable XML files we
extracted ourselves from a local APK (trusted, non-adversarial, not
user-supplied or network-fetched), and the task calls for stdlib-only. Do
not repurpose this parser for untrusted XML without switching to
defusedxml.

Usage:
    python3 vd2svg.py <input.xml> <output.svg>
    python3 vd2svg.py --batch <input_dir> <output_dir>
"""
import os
import sys
import xml.etree.ElementTree as ET

ANDROID_NS = "http://schemas.android.com/apk/res/android"
AAPT_NS = "http://schemas.android.com/aapt"

ns = {"android": ANDROID_NS, "aapt": AAPT_NS}


def a(el, name, default=None):
    return el.get("{%s}%s" % (ANDROID_NS, name), default)


class ConversionError(Exception):
    pass


class GradientCounter:
    def __init__(self):
        self.n = 0

    def next_id(self):
        self.n += 1
        return "grad%d" % self.n


def convert_gradient(gradient_el, gid):
    """Convert an Android <gradient> element to an SVG linear/radialGradient element string."""
    gtype = gradient_el.get("{%s}type" % ANDROID_NS, "linear")
    items = gradient_el.findall("android:item", ns) or gradient_el.findall("item")
    stops = []
    for item in items:
        offset = item.get("{%s}offset" % ANDROID_NS, item.get("offset", "0"))
        color = item.get("{%s}color" % ANDROID_NS, item.get("color", "#000000"))
        argb = color
        opacity = None
        hexpart = argb.lstrip("#")
        if len(hexpart) == 8:
            aa = hexpart[0:2]
            rgb = hexpart[2:8]
            opacity = round(int(aa, 16) / 255.0, 4)
            color_out = "#" + rgb
        else:
            color_out = argb
        stop = '<stop offset="%s" stop-color="%s"%s/>' % (
            offset,
            color_out,
            (' stop-opacity="%s"' % opacity) if opacity is not None else "",
        )
        stops.append(stop)

    if gtype == "radial":
        cx = gradient_el.get("{%s}centerX" % ANDROID_NS, "0")
        cy = gradient_el.get("{%s}centerY" % ANDROID_NS, "0")
        r = gradient_el.get("{%s}gradientRadius" % ANDROID_NS, "1")
        tag = (
            '<radialGradient id="%s" gradientUnits="userSpaceOnUse" cx="%s" cy="%s" r="%s">'
            % (gid, cx, cy, r)
        )
        close = "</radialGradient>"
    else:
        x1 = gradient_el.get("{%s}startX" % ANDROID_NS, "0")
        y1 = gradient_el.get("{%s}startY" % ANDROID_NS, "0")
        x2 = gradient_el.get("{%s}endX" % ANDROID_NS, "0")
        y2 = gradient_el.get("{%s}endY" % ANDROID_NS, "0")
        tag = (
            '<linearGradient id="%s" gradientUnits="userSpaceOnUse" x1="%s" y1="%s" x2="%s" y2="%s">'
            % (gid, x1, y1, x2, y2)
        )
        close = "</linearGradient>"

    return tag + "".join(stops) + close


def argb_to_svg(color):
    """Convert an 8-digit AARRGGBB color to (rgb_hex, opacity) or pass through 6-digit as-is."""
    if color is None:
        return None, None
    hexpart = color.lstrip("#")
    if len(hexpart) == 8:
        aa = hexpart[0:2]
        rgb = hexpart[2:8]
        opacity = int(aa, 16) / 255.0
        if opacity >= 1.0:
            return "#" + rgb, None
        return "#" + rgb, round(opacity, 4)
    return color, None


def convert_path(path_el, defs, gradcounter):
    d = a(path_el, "pathData")
    if d is None:
        raise ConversionError("path missing pathData")

    attrs = ['d="%s"' % d]

    fill_color = a(path_el, "fillColor")
    fill_ref = None

    # gradient fill via <aapt:attr name="android:fillColor"><gradient .../></aapt:attr>
    for aapt_attr in path_el.findall("aapt:attr", ns):
        if aapt_attr.get("name") == "android:fillColor":
            gradient_el = aapt_attr.find("gradient") or aapt_attr.find("android:gradient", ns)
            if gradient_el is not None:
                gid = gradcounter.next_id()
                defs.append(convert_gradient(gradient_el, gid))
                fill_ref = "url(#%s)" % gid

    if fill_ref:
        attrs.append('fill="%s"' % fill_ref)
    elif fill_color is not None:
        rgb, opacity = argb_to_svg(fill_color)
        attrs.append('fill="%s"' % rgb)
        if opacity is not None:
            attrs.append('fill-opacity="%s"' % opacity)
    else:
        # Android default fillColor is #000000 -> SVG default is also black, but be explicit.
        attrs.append('fill="none"')

    fill_alpha = a(path_el, "fillAlpha")
    if fill_alpha is not None and "fill-opacity" not in " ".join(attrs):
        attrs.append('fill-opacity="%s"' % fill_alpha)

    fill_type = a(path_el, "fillType")
    if fill_type == "evenOdd":
        attrs.append('fill-rule="evenodd"')

    stroke_color = a(path_el, "strokeColor")
    if stroke_color is not None:
        srgb, sopacity = argb_to_svg(stroke_color)
        attrs.append('stroke="%s"' % srgb)
        if sopacity is not None:
            attrs.append('stroke-opacity="%s"' % sopacity)

    stroke_width = a(path_el, "strokeWidth")
    if stroke_width is not None:
        attrs.append('stroke-width="%s"' % stroke_width)

    stroke_alpha = a(path_el, "strokeAlpha")
    if stroke_alpha is not None and stroke_color is not None and "stroke-opacity" not in " ".join(attrs):
        attrs.append('stroke-opacity="%s"' % stroke_alpha)

    stroke_linecap = a(path_el, "strokeLineCap")
    if stroke_linecap is not None:
        attrs.append('stroke-linecap="%s"' % stroke_linecap)

    stroke_linejoin = a(path_el, "strokeLineJoin")
    if stroke_linejoin is not None:
        attrs.append('stroke-linejoin="%s"' % stroke_linejoin)

    return "<path %s/>" % " ".join(attrs)


def convert_group(group_el, defs, gradcounter, clipcounter):
    parts = []
    open_tag = "<g>"
    close_tag = "</g>"

    # transforms (not observed in this icon set, but handled best-effort)
    transform_bits = []
    for name, svgname in (
        ("translateX", "translateX"),
        ("translateY", "translateY"),
    ):
        pass  # combined below

    rotate = a(group_el, "rotation")
    scale_x = a(group_el, "scaleX")
    scale_y = a(group_el, "scaleY")
    trans_x = a(group_el, "translateX")
    trans_y = a(group_el, "translateY")
    pivot_x = a(group_el, "pivotX", "0")
    pivot_y = a(group_el, "pivotY", "0")

    transform = []
    if trans_x or trans_y:
        transform.append("translate(%s,%s)" % (trans_x or "0", trans_y or "0"))
    if rotate:
        transform.append("rotate(%s,%s,%s)" % (rotate, pivot_x, pivot_y))
    if scale_x or scale_y:
        transform.append("scale(%s,%s)" % (scale_x or "1", scale_y or "1"))

    clip_id = None
    for clip_el in group_el.findall("clip-path") + group_el.findall("android:clip-path", ns):
        clip_id = "clip%d" % clipcounter.next_id()
        cd = a(clip_el, "pathData")
        defs.append('<clipPath id="%s"><path d="%s"/></clipPath>' % (clip_id, cd))

    g_attrs = []
    if transform:
        g_attrs.append('transform="%s"' % " ".join(transform))
    if clip_id:
        g_attrs.append('clip-path="url(#%s)"' % clip_id)

    open_tag = "<g %s>" % " ".join(g_attrs) if g_attrs else "<g>"
    parts.append(open_tag)

    for child in group_el:
        tag = child.tag.split("}")[-1]
        if tag == "path":
            parts.append(convert_path(child, defs, gradcounter))
        elif tag == "group":
            parts.append(convert_group(child, defs, gradcounter, clipcounter))
        elif tag == "clip-path":
            pass  # already handled above
        else:
            raise ConversionError("unsupported child tag in group: %s" % tag)

    parts.append(close_tag)
    return "".join(parts)


class Counter:
    def __init__(self):
        self.n = 0

    def next_id(self):
        self.n += 1
        return self.n


def convert_file(in_path, out_path):
    tree = ET.parse(in_path)
    root = tree.getroot()
    root_tag = root.tag.split("}")[-1]
    if root_tag != "vector":
        raise ConversionError("root element is <%s>, not <vector> (not a vector drawable)" % root_tag)

    vw = a(root, "viewportWidth")
    vh = a(root, "viewportHeight")
    if vw is None or vh is None:
        raise ConversionError("vector missing viewportWidth/viewportHeight")

    width = a(root, "width", vw + "dp").replace("dp", "")
    height = a(root, "height", vh + "dp").replace("dp", "")

    defs = []
    gradcounter = GradientCounter()
    clipcounter = Counter()

    body_parts = []
    for child in root:
        tag = child.tag.split("}")[-1]
        if tag == "path":
            body_parts.append(convert_path(child, defs, gradcounter))
        elif tag == "group":
            body_parts.append(convert_group(child, defs, gradcounter, clipcounter))
        else:
            raise ConversionError("unsupported root child tag: %s" % tag)

    defs_str = ("<defs>%s</defs>" % "".join(defs)) if defs else ""

    svg = (
        '<svg xmlns="http://www.w3.org/2000/svg" '
        'width="%s" height="%s" viewBox="0 0 %s %s">%s%s</svg>\n'
    ) % (width, height, vw, vh, defs_str, "".join(body_parts))

    with open(out_path, "w") as f:
        f.write(svg)


def main():
    args = sys.argv[1:]
    if len(args) == 3 and args[0] == "--batch":
        in_dir, out_dir = args[1], args[2]
        os.makedirs(out_dir, exist_ok=True)
        ok, failed = [], []
        for fname in sorted(os.listdir(in_dir)):
            if not fname.endswith(".xml"):
                continue
            name = os.path.splitext(fname)[0]
            in_path = os.path.join(in_dir, fname)
            out_path = os.path.join(out_dir, name + ".svg")
            try:
                convert_file(in_path, out_path)
                ok.append(name)
            except Exception as e:
                failed.append((name, str(e)))
                if os.path.exists(out_path):
                    os.remove(out_path)
        print("Converted: %d" % len(ok))
        print("Failed: %d" % len(failed))
        for name, err in failed:
            print("  FAIL %s: %s" % (name, err))
        return

    if len(args) != 2:
        print(__doc__)
        sys.exit(1)
    convert_file(args[0], args[1])


if __name__ == "__main__":
    main()

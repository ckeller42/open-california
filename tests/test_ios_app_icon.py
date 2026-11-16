"""The iPhone app's icon is the PWA's master tile, as iOS needs it: 1024 x 1024, opaque.

.. test:: The iPhone app icon is the PWA master, opaque at 1024 px
   :id: T_IOS_APP_ICON
   :links: R_IOS_LOOK
"""

import json
import os

import pytest

PIL = pytest.importorskip("PIL", reason="Pillow builds the icons; CI has none, the dev suite checks it")
from PIL import Image, ImageChops  # noqa: E402

from tools import make_pwa_icons  # noqa: E402

ICONSET = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "ios",
    "App",
    "Assets.xcassets",
    "AppIcon.appiconset",
)


def test_the_ios_icon_is_opaque_1024_and_fresh():
    """iOS refuses an app icon with transparency and rounds the corners itself, so the icon is the
    master cropped just past its rounded transparent corners; it must equal a fresh build."""
    contents = json.load(open(os.path.join(ICONSET, "Contents.json")))
    name = contents["images"][0]["filename"]
    icon = Image.open(os.path.join(ICONSET, name))
    assert icon.size == (1024, 1024)
    assert icon.mode == "RGB"
    fresh = make_pwa_icons.ios_icon(Image.open(make_pwa_icons.MASTER))
    assert ImageChops.difference(icon, fresh).getbbox() is None, "run: python -m tools.make_pwa_icons"


def test_the_ios_icon_crop_leaves_no_transparent_corner():
    master = Image.open(make_pwa_icons.MASTER).convert("RGBA")
    m = make_pwa_icons.ios_corner_margin(master)
    inner = master.crop((m, m, master.width - m, master.height - m))
    assert inner.getchannel("A").getextrema()[0] == 255
    assert 0 < m < master.width // 10  # only the rounded corner goes, not the art

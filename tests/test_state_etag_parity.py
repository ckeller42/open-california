"""calictl and the satellite leave the same ticking members out of the /api/state ETag
(R_FW_STATE_ETAG): ``calictl.web.STATE_TICKING`` == ``STATE_TICKING[]`` in the firmware's web.c, so
the page's 304 handling sees the same contract from either backend."""

import re
from pathlib import Path

from calictl import web

WEB_C = Path(__file__).resolve().parent.parent / "firmware" / "components" / "cali_core" / "web.c"


def firmware_ticking():
    m = re.search(r"STATE_TICKING\[\]\s*=\s*\{(.*?)\};", WEB_C.read_text(), re.S)
    assert m, "STATE_TICKING[] not found in web.c"
    return set(re.findall(r'"([^"]+)"', m.group(1)))


def test_the_ticking_members_are_the_same_on_calictl_and_the_satellite():
    """One set of ticking members on both backends.

    .. test:: calictl and the firmware leave the same ticking members out of the state ETag
       :id: T_STATE_ETAG_PARITY
       :links: R_FW_STATE_ETAG
    """
    assert firmware_ticking() == set(web.STATE_TICKING)


def test_the_etag_ignores_exactly_the_ticking_scalars():
    base = {"_meta": {"age_s": 1, "online": True}, "ext": {"govee": {"age_s": 5, "temp_c": 16.5}}}
    moved = {"_meta": {"age_s": 99, "online": True}, "ext": {"govee": {"age_s": 50, "temp_c": 16.5}}}
    changed = {"_meta": {"age_s": 1, "online": True}, "ext": {"govee": {"age_s": 5, "temp_c": 16.6}}}
    assert web.state_etag(base) == web.state_etag(moved) != web.state_etag(changed)

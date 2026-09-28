"""tools.wifi_consts.CONSTS: the WiFi AP/setup-portal constants, pinned exactly (#154 Task 1).

.. test:: WiFi setup constants match the pinned values
   :id: T_NET_CONSTS_PINNED
   :links: R_NET_CONSTS_SINGLE_SOURCE
"""
from tools import wifi_consts

WANT = {
    "NET_AP_SSID": "calictl-esp-setup",
    "NET_AP_PSK": "calictl-setup",
    "NET_HOSTNAME": "calictl-esp",
    "NET_AP_ADDR": "192.168.4.1",
    "NET_AP_ADDR_U32": 0xC0A80401,
    "NET_AP_CLOSE_MS": 30000,
    "NET_RETRY_MIN_MS": 1000,
    "NET_RETRY_MAX_MS": 60000,
    "NET_SETUP_AFTER_MS": 300000,
    "NET_PAGE_POLL_MS": 2000,
    "NET_HTTP_PORT": 80,
    "NET_HTTP_REQ_MAX": 2048,
    "NET_HTTP_BODY_MAX": 16384,
    "NET_JSON_MAX": 8192,
    "NET_SSID_MAX": 32,
    "NET_PSK_MIN": 8,
    "NET_PSK_MAX": 63,
    "NET_SCAN_MAX": 16,
}


def test_consts_match_pinned_values_exactly():
    assert wifi_consts.CONSTS == WANT


def test_ap_addr_string_and_u32_agree():
    octets = [int(o) for o in wifi_consts.CONSTS["NET_AP_ADDR"].split(".")]
    assert len(octets) == 4
    packed = (octets[0] << 24) | (octets[1] << 16) | (octets[2] << 8) | octets[3]
    assert packed == wifi_consts.CONSTS["NET_AP_ADDR_U32"]


def test_psk_bounds_are_sane_for_wpa2():
    assert 8 <= wifi_consts.CONSTS["NET_PSK_MIN"] <= wifi_consts.CONSTS["NET_PSK_MAX"] <= 63

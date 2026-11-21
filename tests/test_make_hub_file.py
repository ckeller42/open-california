"""tools/make_hub_file.py builds the oc-hub.json that the Hub form's "Load from file" reads (#555)."""

import pytest

from tools.make_hub_file import ENV, build

URL = "wss://h.example:8084/mqtt"
ENVV = {v: f"val-{k}" for k, v in ENV.items()}


def test_the_file_has_the_api_hub_keys_and_the_format():
    d = build(URL, ENVV)
    assert d == {"format": "oc-hub/1", "url": URL, "van": "val-van", "user": "val-user", "pass": "val-pass",
                 "app_user": "val-app_user", "app_pass": "val-app_pass"}


def test_lora_adds_the_switch():
    assert build(URL, ENVV, lora=True)["lora"] is True
    assert "lora" not in build(URL, ENVV)


KEY = "NNSXS.ABCDEFGHIJ234567.KLMNOPQRSTUV234567"
TENANT = "linsel.eu1.cloud.thethings.industries"


def test_ttn_cluster_adds_the_app_lora_settings():
    d = build(URL, {**ENVV, "OC_HUB_TTN_KEY": KEY}, lora=True, ttn_cluster=TENANT)
    assert d["ttn"] == {"cluster": TENANT, "key": KEY} and d["lora"] is True
    assert "ttn" not in build(URL, {**ENVV, "OC_HUB_TTN_KEY": KEY})


@pytest.mark.parametrize("cluster,key,why", [
    ("https://eu1.cloud.thethings.network", KEY, "cluster"),
    ("router.fritz.box", KEY, "cluster"),
    (TENANT, "", "OC_HUB_TTN_KEY"),
    (TENANT, "not-a-key", "OC_HUB_TTN_KEY"),
])
def test_ttn_refuses_what_the_app_would_refuse(cluster, key, why):
    with pytest.raises(ValueError, match=why):
        build(URL, {**ENVV, "OC_HUB_TTN_KEY": key}, ttn_cluster=cluster)


@pytest.mark.parametrize("url,env,why", [
    ("https://h.example", ENVV, "wss://"),
    (URL, {**ENVV, "OC_HUB_APP_PASS": ""}, "OC_HUB_APP_PASS"),
    (URL, {**ENVV, "OC_HUB_VAN_PASS": "a*b"}, "*"),
])
def test_refuses_what_the_firmware_would_refuse(url, env, why):
    with pytest.raises(ValueError, match=why.replace("*", r"\*")):
        build(url, env)

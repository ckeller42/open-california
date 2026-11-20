"""tools/make_hub_file.py builds the oc-hub.json that the Hub form's "Load from file" reads (#555)."""

import pytest

from tools.make_hub_file import ENV, build

URL = "wss://h.example:8084/mqtt"
ENVV = {v: f"val-{k}" for k, v in ENV.items()}


def test_the_file_has_the_api_hub_keys_and_the_format():
    d = build(URL, ENVV)
    assert d == {"format": "oc-hub/1", "url": URL, "van": "val-van", "user": "val-user", "pass": "val-pass",
                 "app_user": "val-app_user", "app_pass": "val-app_pass"}


@pytest.mark.parametrize("url,env,why", [
    ("https://h.example", ENVV, "wss://"),
    (URL, {**ENVV, "OC_HUB_APP_PASS": ""}, "OC_HUB_APP_PASS"),
    (URL, {**ENVV, "OC_HUB_VAN_PASS": "a*b"}, "*"),
])
def test_refuses_what_the_firmware_would_refuse(url, env, why):
    with pytest.raises(ValueError, match=why.replace("*", r"\*")):
        build(url, env)

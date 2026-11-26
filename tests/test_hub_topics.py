"""The remote hub's MQTT topics in code equal the broker permission table of docs/howto-remote-hub.md.

The EMQX ACL ends in "deny all": a subscribe outside a user's rows is refused SILENTLY (the client
sees a granted SUBSCRIBE and then never a message). So the app (hub.js), the satellite
(cali_hub/hub_esp.c) and the bridge (bridge/src/index.js) must use exactly the topics the table
gives their user. Topics are written below ``oc/<van>/``.
"""

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DOC = (ROOT / "docs" / "howto-remote-hub.md").read_text()
HUB_JS = (ROOT / "calictl" / "webui" / "hub.js").read_text()
HUB_ESP = (ROOT / "firmware" / "components" / "cali_hub" / "hub_esp.c").read_text()
BRIDGE = (ROOT / "bridge" / "src" / "index.js").read_text()


def doc_rows():
    """{user: {"publish": set, "subscribe": set}} from the table rows "| `user` | who | publish ...; subscribe ... |"."""
    rows = {}
    for user, perms in re.findall(r"^\s*\| `(van|app|bridge)` \| [^|]+ \| ([^|]+) \|\s*$", DOC, re.M):
        got = {"publish": set(), "subscribe": set()}
        for part in perms.split(";"):
            verb, _, topics = part.strip().partition(" ")
            for t in re.findall(r"`oc/<van>/([^`]+)`", topics):
                got[verb.replace(" (retained)", "")].add(t)
        rows[user] = got
    return rows


def app_topics():
    """hub.js: the subscribe list of the connect handler and every topic it publishes to."""
    m = re.search(r"c\.subscribe\(\[([^\]]*)\]\)", HUB_JS)
    assert m, "hub.js: the subscribe call moved; update this test"
    subs = set(re.findall(r't \+ "([^"]+)"', m.group(1)))
    pubs = set(re.findall(r'\.publish\(hubTopic\(cfg, "([^"]+)"\)', HUB_JS))
    return {"publish": pubs, "subscribe": subs}


def van_topics():
    """hub_esp.c: the topic table SUF (index = CALI_HUB_T_STATE, CALI_HUB_T_RES, T_REQ, T_ONLINE)."""
    suf = re.search(r"SUF\[T_N\] = \{([^}]*)\}", HUB_ESP)
    assert suf, "hub_esp.c: the topic table moved; update this test"
    state, res, req, online = re.findall(r'"([^"]+)"', suf.group(1))
    subs = set(re.findall(r"esp_mqtt_client_subscribe\(c->h, c->topic\[(\w+)\]", HUB_ESP))
    pubs = set(re.findall(r"esp_mqtt_client_publish\(c->h, c->topic\[([\w.]+)\]", HUB_ESP))
    names = {"T_REQ": req, "T_ONLINE": online}
    assert pubs == {"o.topic", "T_ONLINE"}, pubs  # the out queue (state, res) and the presence flag
    assert "last_will = {.topic = c->topic[T_ONLINE]" in HUB_ESP
    return {"publish": {state, res, online}, "subscribe": {names[s] for s in subs}}


def test_the_doc_has_the_three_rows():
    assert set(doc_rows()) == {"van", "app", "bridge"}


def test_app_uses_exactly_its_permissions():
    assert app_topics() == doc_rows()["app"]


def test_van_uses_exactly_its_permissions():
    assert van_topics() == doc_rows()["van"]


def test_bridge_publishes_inside_its_permission_and_the_app_hears_it():
    assert re.search(r"topic: `oc/\$\{env\.VAN\}/lora/\$\{u\.f_port\}`", BRIDGE), "bridge topic moved"
    assert doc_rows()["bridge"] == {"publish": {"lora/#"}, "subscribe": set()}
    assert "lora/+" in app_topics()["subscribe"]  # one level: lora/<fport>


def test_the_doc_never_tells_the_app_user_to_subscribe_a_wildcard_it_lacks():
    """The ACL refuses ``oc/<van>/#`` for ``app`` without an error: a smoke test that does so sees nothing."""
    bad = [
        line.strip()
        for line in DOC.splitlines()
        if "app" in line and re.search(r"subscribe[^.]*oc/(<van>|‹van›)/#", line)
    ]
    assert not bad, bad

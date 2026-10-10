"""The device build must answer the unit's own ATT requests (#264, field 2026-10-10)."""

from pathlib import Path

SDKCONFIG = Path(__file__).resolve().parents[2] / "firmware" / "sdkconfig.defaults"


def test_device_build_keeps_a_gatt_server_to_answer_the_units_mtu_request():
    """The unit sends every central an ATT Exchange MTU Request; esp-nimble without a GATT server
    drops it unanswered and the unit hangs up after its 30 s ATT timeout (HCI 0x13). IDF v6.1 ties
    BT_NIMBLE_GATT_SERVER to the peripheral role, so both stay on (= host syscfg).

    .. test:: The ESP build keeps NimBLE's GATT server so the unit's MTU request is answered
       :id: T_FW_GATT_SERVER_ON
       :links: R_FAKE_UNIT_FIDELITY
    """
    lines = set(SDKCONFIG.read_text().splitlines())
    assert "CONFIG_BT_NIMBLE_ROLE_PERIPHERAL=y" in lines
    assert "CONFIG_BT_NIMBLE_GATT_SERVER=y" in lines
    assert "CONFIG_BT_NIMBLE_ROLE_PERIPHERAL=n" not in lines

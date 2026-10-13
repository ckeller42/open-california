"""The status display's touch read never aborts the firmware (#346).

``display.c`` is ESP-only (no LVGL on the host), so this guards its source: esp_lvgl_port's own touch
read ``ESP_ERROR_CHECK``s the I2C read, and one transient error on the bus shared with the LTR-553
and AXP2101 aborted the whole firmware (25+ panic reboots, backtrace via ``R_FW_PANIC_CAPTURE``).

.. test:: display.c reads the touch controller itself, never with an abort-on-error check, against the pinned port
   :id: T_FW_TOUCH_NO_ABORT
   :links: R_FW_TOUCH_NO_ABORT
"""

import re
from pathlib import Path

DISPLAY = Path(__file__).resolve().parents[1] / "firmware/components/cali_display"


def test_display_never_aborts_on_an_esp_error():
    src = (DISPLAY / "display.c").read_text()
    code = re.sub(r"/\*.*?\*/", "", src, flags=re.S)
    assert "ESP_ERROR_CHECK" not in code and "abort(" not in code
    assert "esp_lcd_touch_read_data(s_tp)" in code  # not the port's read
    assert 'cali_diag_evt("touch"' in code  # the error count reaches the log


def test_the_private_touch_context_layout_is_pinned():
    """display.c reads the handle as the first member of the port's private context; that layout was
    read from esp_lvgl_port 2.9.0, so the manifest pins exactly that version."""
    manifest = (DISPLAY / "idf_component.yml").read_text()
    assert 'espressif/esp_lvgl_port: "==2.9.0"' in manifest
    assert "esp_lvgl_port 2.9.0" in (DISPLAY / "display.c").read_text()

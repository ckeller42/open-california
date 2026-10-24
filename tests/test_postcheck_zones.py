from calictl import control, postcheck


def test_applied_check_reads_the_zone_the_write_targets():
    """roof-reading is L9 and kitchen-cabinet L6 (DEVICE-proven); every LIGHT_ZONES key maps to its lamp."""
    nums = postcheck._light_zone_num()
    assert nums["roof-reading"] == 9 and nums["kitchen-cabinet"] == 6 and nums["entrance"] == 12
    assert set(nums) == set(control.LIGHT_ZONES)

import CCodec
import CControl
import Testing

/// Compile-time check of the narrow module maps (`csrc/swiftpm`, `firmware/components/cali_core/swiftpm`):
/// each symbol below is part of the C API the app links. A header that left the map breaks this build.
/// Swift cannot test for absence in a test: that `cali_session.h` and `codec_dict.h` stay hidden was
/// checked by hand (`cali_session_water_held()` and `CODEC_F_HAS_DEFAULT` fail with "cannot find ... in scope").
struct ModuleSurfaceTests {
    /// - Test: T_IOS_MODULE_SURFACE
    /// - Verifies: R_IOS_TESTED, R_IOS_FUNCTIONS
    @Test func linkedApiIsVisible() {
        _ = codec_func_by_name("water")  // codec.h
        _ = CODEC_ARM_DELAY_MS  // codec_chars.h
        _ = CALI_CTL_CHARS  // control_consts.h
        _ = freshness_settle  // ports.h
        _ = cali_ctl_plan  // cali_control.h
        _ = CALI_CTL_DEADLINE_MS  // cali_control.h
    }
}

import CCodec
import Foundation

/// The tank levels of one `1302` frame, as `ports.h` reads them: the raw `Level` fields (liters).
public struct WaterLevels: Equatable, Sendable {
    public var fresh: UInt32?
    public var waste: UInt32?
    /// `FreshWaterInfoPopUp` 5: the unit's measured-empty alarm, never the stale latch.
    public var empty: Bool

    public init(fresh: UInt32?, waste: UInt32?, empty: Bool = false) {
        self.fresh = fresh
        self.waste = waste
        self.empty = empty
    }

    /// From a decoded `water` frame (session.c `water_levels`).
    public init(fields: [String: UInt32]) {
        self.init(fresh: fields["FreshWaterLevel"], waste: fields["WasteWaterLevel"], empty: fields["FreshWaterInfoPopUp"] == 5)
    }
}

/// The water stale-latch guard of the satellite and calictl (`csrc/ports.c` `freshness_settle`,
/// `freshness.settle_water`): a parked unit serves a 1 L latch, and a measurement ramps 1 -> real
/// level in about 4 s. A new level is adopted only once the same reading has held for 5 s; until
/// then the last plausible reading stays. Feed every `1302` frame, read or push, in arrival order.
///
/// - Requirement: R_IOS_HONEST, R_IOS_FUNCTIONS
public struct WaterGuard: Sendable {
    /// The last plausible reading; nil on a cold start.
    public private(set) var baseline: WaterLevels?
    private var pending = fresh_pending_t()

    public init(baseline: WaterLevels? = nil) { self.baseline = baseline }

    /// True: adopt `new` as the baseline (show it). False: hold the baseline (show it, or nothing).
    public mutating func settle(_ new: WaterLevels, nowMs: UInt64) -> Bool {
        func i(_ v: UInt32?) -> Int32 { Int32(truncatingIfNeeded: v ?? 0) }
        let adopt = freshness_settle(
            i(new.fresh), i(baseline?.fresh), i(new.waste), i(baseline?.waste),
            Self.have(new, baseline), nowMs, FRESH_SETTLE_MS, &pending) == 1
        if adopt { baseline = new }
        return adopt
    }

    /// `freshness_implausible_drop` alone: true = `new` is the stale latch next to `prev`.
    public static func implausibleDrop(_ new: WaterLevels, prev: WaterLevels) -> Bool {
        func i(_ v: UInt32?) -> Int32 { Int32(truncatingIfNeeded: v ?? 0) }
        return freshness_implausible_drop(i(new.fresh), i(prev.fresh), i(new.waste), i(prev.waste), have(new, prev)) == 1
    }

    private static func have(_ new: WaterLevels, _ prev: WaterLevels?) -> UInt8 {
        var h: UInt8 = 0
        if new.fresh != nil { h |= UInt8(FRESH_HAVE_NF) }
        if prev?.fresh != nil { h |= UInt8(FRESH_HAVE_PF) }
        if new.waste != nil { h |= UInt8(FRESH_HAVE_NG) }
        if prev?.waste != nil { h |= UInt8(FRESH_HAVE_PG) }
        if new.empty { h |= UInt8(FRESH_NEW_EMPTY) }
        return h
    }
}

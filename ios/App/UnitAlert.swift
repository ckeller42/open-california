import CaliCore
import SwiftUI

/// One alert the camper unit reports now, carrying the same fault text the function's screen shows.
/// The `id` is stable and function-scoped (`"energy:<flag>"`, `"water:fresh:<v>"`, `"water:waste:<v>"`,
/// `"cooler:<v>"`, `"heater:<v>"`), so a re-posted notification replaces rather than stacks.
///
/// - Requirement: R_IOS_ALERTS
struct UnitAlert: Equatable, Identifiable {
    let id: String
    let body: LocalizedStringResource
}

/// The unit's active alerts and the rising edges between two reads. Pure read of the store; the
/// notifier and the Setup flag live elsewhere, so this core is unit-tested without the system.
///
/// - Requirement: R_IOS_ALERTS
enum UnitAlertMonitor {
    /// Every alert the unit flags now, from the screens' own fault maps. An energy flag with no map
    /// entry keeps the screen's fallback text (never an empty body); an unknown water/cooler/heater
    /// value yields no alert at all.
    @MainActor static func alerts(_ store: CamperStore) -> [UnitAlert] {
        var out: [UnitAlert] = []
        if case .array(let flags)? = store.value("energy", "faults") {
            for flag in flags.compactMap(\.string) {
                out.append(
                    UnitAlert(
                        id: "energy:\(flag)",
                        body: EnergyView.faultText[flag] ?? "Unknown fault of the energy system"))
            }
        }
        if let v = store.value("water", "fresh_alert")?.string, let text = WaterView.freshAlert[v] {
            out.append(UnitAlert(id: "water:fresh:\(v)", body: text))
        }
        if let v = store.value("water", "waste_alert")?.string, let text = WaterView.wasteAlert[v] {
            out.append(UnitAlert(id: "water:waste:\(v)", body: text))
        }
        if let v = store.value("cooler", "fault")?.string, let text = CoolerView.faultText[v] {
            out.append(UnitAlert(id: "cooler:\(v)", body: text))
        }
        if let v = store.value("airheater", "error")?.string, let text = AirHeaterView.errorText[v] {
            out.append(UnitAlert(id: "heater:\(v)", body: text))
        }
        return out
    }

    /// The alerts whose id is not already in `old` — the ones that just appeared, to post.
    static func rising(from old: Set<String>, to new: [UnitAlert]) -> [UnitAlert] {
        new.filter { !old.contains($0.id) }
    }
}

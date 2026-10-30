import CaliCore
import SwiftUI

/// The energy screen: the web UI's energy rows (labels, units and order), each source only when
/// installed. "House load" is left out: the web UI shows it only with the satellite's solar charger
/// readings, which this app does not read, so the sum would be incomplete.
///
/// - Requirement: R_IOS_FUNCTIONS, R_IOS_INSTALLED, R_IOS_I18N
struct EnergyView: View {
    let store: CamperStore

    /// `semantics.js` SEM_SRC_STATE words.
    static let sourceState: [String: LocalizedStringResource] = [
        "active": "active", "inactive": "inactive", "standby": "standby", "init": "init", "error": "error",
    ]
    /// The unit's own dialog texts per energy fault flag (web UI `ENERGY_FAULT_MSG`).
    static let faultText: [String: LocalizedStringResource] = [
        "WarningLevelActive": "Battery is flat. Charge now!",
        "SystemError": "Something wrong with second battery. Please visit a workshop.",
        "SleepWarning": "The charging cable is still plugged into the vehicle's external socket. Please remove it.",
        "CurrentDeratingTemperature": "Charging current is limited. Ambient temperature is too high.",
        "LandDefect": "External power source device not functioning. Please visit a workshop.",
        "DcdcDefect": "Charging restricted while the vehicle is moving. Please visit a workshop.",
        "PvDefect": "Solar inverter not working. Please visit a workshop.",
        "TwoBattNotCharged": "Battery is not charging. Please reduce power consumption.",
        "TwoBattSwitchAtWorkshop": "Additional battery: deep discharge protection is active. Please visit a workshop.",
        "TwoBattSwitchAtCharging": "Additional battery: charging required. Power consumers will switch off.",
        "LandNotAvailable": "External power source is currently unavailable.",
        "WarningLevelTwo": "Second battery warning level 2",
    ]

    private func num(_ key: String) -> Double? { store.value("energy", key)?.number }

    /// "W · A · V" like the web UI's `wav`; W = V × A.
    private func wav(_ v: Double?, _ a: Double?) -> String {
        guard v != nil || a != nil else { return "—" }
        let w = v.flatMap { v in a.map { String(Int((v * $0).rounded())) } } ?? "—"
        return "\(w) W · \(a?.formatted() ?? "—") A · \(v?.formatted() ?? "—") V"
    }

    private func pct(_ key: String) -> String { num(key).map { "\(Int($0))%" } ?? "—" }

    /// A charger row: "<state> (<W> W · <A> A)".
    private func source(_ prefix: String) -> Text {
        let st = store.value("energy", "\(prefix)_state")?.string
        let word = st.map { Self.sourceState[$0].map { String(localized: $0) } ?? $0 } ?? "—"
        let w = num("\(prefix)_power").map { String(Int($0)) } ?? "—"
        let a = num("\(prefix)_current")?.formatted() ?? "—"
        return Text(verbatim: "\(word) (\(w) W · \(a) A)")
    }

    var body: some View {
        StatusPage(store: store) {
            if store.state.interpreted["energy"] != nil {
                VStack(spacing: 0) {
                    Row("Second battery", Text(verbatim: pct("soc2_pct")), bar: num("soc2_pct"))
                    Row("Second battery power", Text(verbatim: wav(num("batt2_v"), num("batt2_current"))))
                    Row("Time remaining", Text(verbatim: num("batt2_remaining_h").map { "\(Int($0)) h" } ?? "—"))
                    Row("Starter battery", Text(verbatim: pct("soc1_pct")), bar: num("soc1_pct"))
                    Row("Starter power", Text(verbatim: wav(num("batt1_v"), num("batt1_current"))))
                    if store.value("energy", "dcdc_installed")?.bool == true { Row("Vehicle power", source("dcdc")) }
                    if store.value("energy", "shore_installed")?.bool == true { Row("Shore power", source("shore")) }
                    if store.value("energy", "solar_installed")?.bool == true { Row("Solar power", source("solar")) }
                    Row("Issues", issues)
                    Row("Starter data age", starterAge)
                }
                .background(Color(uiColor: .secondarySystemBackground), in: RoundedRectangle(cornerRadius: 16))
            }
        }
    }

    private var issues: Text {
        guard case .array(let flags)? = store.value("energy", "faults"), !flags.isEmpty else { return Text("none") }
        let texts = flags.compactMap(\.string).map { Self.faultText[$0].map { String(localized: $0) } ?? $0 }
        return Text(Image(systemName: "exclamationmark.triangle")) + Text(verbatim: " " + texts.joined(separator: " · "))
    }

    /// The unit reports an age only for the starter battery; 255 = the starter subsystem sleeps.
    private var starterAge: Text {
        if store.value("energy", "stale")?.bool == true {
            return Text("stale (starter asleep)")
        }
        return Text(verbatim: num("age_min").map { "\(Int($0)) min" } ?? "—")
    }
}

/// A label and its value, with an optional level bar (0–100).
///
/// - Requirement: R_IOS_LOOK
struct Row: View {
    let label: LocalizedStringResource
    let value: Text
    var bar: Double?

    init(_ label: LocalizedStringResource, _ value: Text, bar: Double? = nil) {
        self.label = label
        self.value = value
        self.bar = bar
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 6) {
            HStack(alignment: .firstTextBaseline) {
                Text(label)
                Spacer(minLength: 12)
                value.fontWeight(.medium)
                    .multilineTextAlignment(.trailing)
            }
            if let bar {
                ProgressView(value: min(max(bar, 0), 100), total: 100)
                    .accessibilityHidden(true)
            }
        }
        .padding(.horizontal)
        .padding(.vertical, 10)
        .accessibilityElement(children: .combine)
    }
}

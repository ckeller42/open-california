import CaliCore
import SwiftUI

/// The energy screen: the web UI's energy rows (labels, units and order), each source only when
/// installed. "House load" is left out: the web UI shows it only with the satellite's solar charger
/// readings, which this app does not read, so the sum would be incomplete.
///
/// - Requirement: R_IOS_FUNCTIONS, R_IOS_INSTALLED, R_IOS_I18N
struct EnergyView: View {
    let store: CamperStore
    @Environment(\.locale) private var locale

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

    /// "W · A · V" like the web UI's `wav`; W = V × A. The second string is the same in full words.
    private func wav(_ v: Double?, _ a: Double?) -> (String, String) {
        guard v != nil || a != nil else { return ("—", "—") }
        let w = v.flatMap { v in a.map { (v * $0).rounded() } }
        let text = "\(w.map { String(Int($0)) } ?? "—")\(nbsp)W\(nbsp)· \(a.map(fmt) ?? "—")\(nbsp)A\(nbsp)· \(v.map(fmt) ?? "—")\(nbsp)V"
        let words = [w.map { spoken($0, UnitPower.watts, locale) }, a.map { spoken($0, UnitElectricCurrent.amperes, locale) },
                     v.map { spoken($0, UnitElectricPotentialDifference.volts, locale) }].map { $0 ?? "—" }
        return (text, words.joined(separator: ", "))
    }

    /// A number in the view's locale ("14.1" / "14,1").
    private func fmt(_ x: Double) -> String { numberText(x, locale) }

    private func pct(_ key: String) -> String { num(key).map { "\(Int($0))%" } ?? "—" }

    /// A charger row: "<state> (<W> W · <A> A)", and the same in full words.
    private func source(_ prefix: String) -> (String, String) {
        let st = store.value("energy", "\(prefix)_state")?.string
        let word = st.map { Self.sourceState[$0].map { $0.string(locale) } ?? $0 } ?? "—"
        let w = num("\(prefix)_power"), a = num("\(prefix)_current")
        return ("\(word) (\(w.map { String(Int($0)) } ?? "—")\(nbsp)W\(nbsp)· \(a.map(fmt) ?? "—")\(nbsp)A)",
                [word, w.map { spoken($0, UnitPower.watts, locale) } ?? "—",
                 a.map { spoken($0, UnitElectricCurrent.amperes, locale) } ?? "—"].joined(separator: ", "))
    }

    var body: some View {
        StatusPage(store: store) {
            if store.state.interpreted["energy"] != nil {
                EnergyModeView(store: store)
                // The unit's starter data is stale while the starter subsystem sleeps (age 255).
                let starterStale = store.value("energy", "stale")?.bool == true
                let batt2 = wav(num("batt2_v"), num("batt2_current")), batt1 = wav(num("batt1_v"), num("batt1_current"))
                VStack(spacing: 0) {
                    Row("Second battery", Text(verbatim: pct("soc2_pct")), bar: num("soc2_pct"))
                    Row("Second battery power", Text(verbatim: batt2.0), spoken: batt2.1)
                    Row("Time remaining", Text(verbatim: num("batt2_remaining_h").map { "\(Int($0))\(nbsp)h" } ?? "—"),
                        spoken: num("batt2_remaining_h").map { spoken($0, UnitDuration.hours, locale) })
                    Row("Starter battery", Text(verbatim: pct("soc1_pct")), bar: num("soc1_pct"), dimmed: starterStale)
                    Row("Data age", starterAge(starterStale), spoken: starterAgeSpoken(starterStale), dimmed: starterStale, clock: starterStale)
                    Row("Starter power", Text(verbatim: batt1.0), spoken: batt1.1, dimmed: starterStale)
                    if store.value("energy", "dcdc_installed")?.bool == true {
                        let r = source("dcdc")
                        Row("Vehicle power", Text(verbatim: r.0), spoken: r.1)
                    }
                    if store.value("energy", "shore_installed")?.bool == true {
                        let r = source("shore")
                        Row("Shore power", Text(verbatim: r.0), spoken: r.1)
                    }
                    if store.value("energy", "solar_installed")?.bool == true {
                        let r = source("solar")
                        Row("Solar power", Text(verbatim: r.0), spoken: r.1)
                    }
                    if faults.isEmpty { Row("Issues", Text("none")) }
                }
                .background(Color(uiColor: .secondarySystemBackground), in: RoundedRectangle(cornerRadius: 16))
                ForEach(faults.indices, id: \.self) { AlertCard(text: faults[$0]) }
            }
        }
    }

    /// The unit's fault dialogs (one alert card each).
    private var faults: [LocalizedStringResource] { Self.faults(store) }

    static func faults(_ store: CamperStore) -> [LocalizedStringResource] {
        guard case .array(let flags)? = store.value("energy", "faults") else { return [] }
        return flags.compactMap(\.string).map { Self.faultText[$0] ?? LocalizedStringResource(String.LocalizationValue($0)) }
    }

    /// The unit reports an age only for the starter battery; 255 = the starter subsystem sleeps.
    private func starterAge(_ stale: Bool) -> Text {
        if stale { return Text("stale (starter asleep)") }
        return Text(verbatim: num("age_min").map { "\(Int($0))\(nbsp)min" } ?? "—")
    }

    private func starterAgeSpoken(_ stale: Bool) -> String? {
        if stale { return LocalizedStringResource("stale (starter asleep)").string(locale) }
        return num("age_min").map { spoken($0, UnitDuration.minutes, locale) }
    }
}

/// A label and its value, with an optional level bar (0–100). Stale data (the page's or `dimmed`)
/// draws the value muted; VoiceOver reads `spoken` (full unit words) when given.
///
/// - Requirement: R_IOS_LOOK, R_IOS_HONEST
struct Row: View {
    let label: LocalizedStringResource
    let value: Text
    var spoken: String?
    var bar: Double?
    var dimmed: Bool
    /// A clock symbol before the value: a stale reading.
    var clock: Bool
    @Environment(\.dynamicTypeSize) private var typeSize
    @Environment(\.pageStale) private var pageStale
    @Environment(\.pageStaleSpoken) private var staleSpoken

    init(_ label: LocalizedStringResource, _ value: Text, spoken: String? = nil, bar: Double? = nil, dimmed: Bool = false, clock: Bool = false) {
        self.clock = clock
        self.label = label
        self.value = value
        self.spoken = spoken
        self.bar = bar
        self.dimmed = dimmed
    }

    private func shownValue(_ muted: Bool) -> some View {
        HStack(alignment: .firstTextBaseline, spacing: 4) {
            if clock { Image(systemName: "clock") }
            value.fontWeight(.medium)
        }
        .foregroundStyle(muted ? Color.staleText : Color.primary)
    }

    var body: some View {
        let muted = pageStale || dimmed
        VStack(alignment: .leading, spacing: 6) {
            // Side by side; at accessibility text sizes the value goes under its label, so no word breaks.
            if typeSize.isAccessibilitySize {
                VStack(alignment: .leading, spacing: 2) {
                    Text(label)
                    shownValue(muted)
                }
                .frame(maxWidth: .infinity, alignment: .leading)
            } else {
                HStack(alignment: .firstTextBaseline) {
                    Text(label)
                    Spacer(minLength: 12)
                    shownValue(muted).multilineTextAlignment(.trailing)
                }
            }
            if let bar {
                ProgressView(value: min(max(bar, 0), 100), total: 100)
                    .tint(muted ? Color(uiColor: .systemGray2) : Color.accentColor)
                    .accessibilityHidden(true)
            }
        }
        .padding(.horizontal)
        .padding(.vertical, 10)
        .accessibilityElement(children: .combine)
        .accessibilityLabel(Text(label))
        .accessibilityValue((spoken.map { Text(verbatim: $0) } ?? value) + Text(verbatim: staleSpoken.map { ", \($0)" } ?? ""))
    }
}

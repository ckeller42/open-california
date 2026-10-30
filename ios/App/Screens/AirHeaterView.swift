import CaliBLE
import SwiftUI

/// The air heater (app.js FEATURES.airheater) — the only place in the app that controls it (no
/// Siri, no cockpit toggle). The app's gates (`R_AIRHEATER_APP_GATES`): immediate heating is never
/// greyed (the planner refuses ON under ErrorCode 5, with the reason); continuous heating can only be
/// turned off here; the run time is locked during continuous heating. Every write but continuous
/// heating OFF asks first, as the web UI does (fuel burner, not live-verified). The departure timer
/// is not in this app yet.
///
/// - Requirement: R_IOS_FUNCTIONS, R_IOS_SIRI_NO_HEATER, R_AIRHEATER_APP_GATES, R_IOS_I18N
struct AirHeaterView: View {
    let store: CamperStore

    static let runtimes = stride(from: 10, through: 120, by: 10)

    /// `AIRHEATER_ERROR_MSG` without the ⚠ glyph.
    static let errorText: [String: LocalizedStringResource] = [
        "low_battery": "Battery voltage too low — charge the second battery or connect external power",
        "low_fuel": "Low fuel — the heater switched off, please refuel",
        "system_error": "Auxiliary air heater fault — please contact your workshop",
        "heating_time_exceeded": "Emission limit exceeded — the heater switched off, drive ≥ 5 km/h to re-enable",
        "not_possible": "Heater deactivated — not while the engine or the auxiliary water heater is running",
        "engine_running": "Auxiliary air heater deactivated due to engine running.",
        "aux_heater_active": "Auxiliary air heater deactivated due to activation of the auxiliary heater.",
        "unknown": "Heater error",
    ]

    static func power(_ on: Bool) -> Command { Command(function: "airheater", what: "power", value: on ? "on" : "off") }
    static let permanentOff = Command(function: "airheater", what: "permanent", value: "off")
    static func level(_ n: Int) -> Command { Command(function: "airheater", what: "level", value: String(n)) }
    static func runtime(_ min: Int) -> Command { Command(function: "airheater", what: "runtime", value: String(min)) }

    /// The web UI's feature-level prompt for a write `what` (every one but continuous heating).
    private static func fuel(_ what: String) -> LocalizedStringResource {
        "Start the fuel-burning auxiliary air heater (\(what))? It is not live-verified. Continue?"
    }

    private func flag(_ key: String) -> Bool { store.value("airheater", key)?.bool == true }
    private func num(_ key: String) -> Int? { store.value("airheater", key)?.number.map(Int.init) }

    /// The heater's own status line (app.js `summary`, without the timer).
    private var status: LocalizedStringResource {
        guard flag("running") else { return "Inactive" }
        if flag("permanent") { return "Active • Continuous heating" }
        if let n = num("running_time_remaining") { return "Active • \(n) min remaining" }
        return "Active"
    }

    var body: some View {
        ControlPage(store: store, function: "airheater", title: "Air heater") { run in
            let permanent = flag("permanent")
            if let err = store.value("airheater", "error")?.string {
                AlertCard(text: Self.errorText[err] ?? "Heater error")
            }
            Card {
                Row("Status", Text(status))
            }
            Card {
                ControlRow {
                    SwitchRow(label: Text("Immediate heating"), isOn: Binding(get: { flag("normal") }, set: { on in
                        run(Self.power(on), confirm: [
                            on && permanent ? "Turn off continuous heating? You can only turn continuous heating on again using the controls in your California." : nil,
                            Self.fuel("power")])
                    }))
                }
                ControlRow(disabledBecause: permanent ? nil : "Can only be started from inside the vehicle") {
                    SwitchRow(label: Text("Continuous heating"), isOn: Binding(get: { permanent }, set: { on in
                        if !on { run(Self.permanentOff, confirm: ["Turn off continuous heating? It can only be turned back on from inside the vehicle. Continue?"]) }
                    }))
                }
                ControlRow {
                    MenuPicker(label: Text("Heating temperature"), selection: Binding(get: { num("level") ?? 0 }, set: { run(Self.level($0), confirm: [Self.fuel("level")]) })) {
                        if num("level").map({ !(1...10).contains($0) }) ?? true { Text(verbatim: "—").tag(num("level") ?? 0) }
                        ForEach(1...10, id: \.self) { n in
                            if n == 10 { Text(verbatim: "HI").tag(n) } else { Text("Level \(n)").tag(n) }
                        }
                    }
                }
                ControlRow(disabledBecause: permanent ? "Not available during continuous heating" : nil) {
                    MenuPicker(label: Text("Run time"), selection: Binding(get: { num("running_time") ?? 0 }, set: { n in
                        run(Self.runtime(n), confirm: [
                            flag("running") ? "Apply new settings now? Resetting the duration. The heating will stay on for \(n) minutes." : nil,
                            Self.fuel("runtime")])
                    })) {
                        if !Self.runtimes.contains(num("running_time") ?? -1) { Text(verbatim: "—").tag(num("running_time") ?? 0) }
                        ForEach(Array(Self.runtimes), id: \.self) { Text(verbatim: "\($0)\(nbsp)min").tag($0) }
                    }
                }
            }
        }
    }
}

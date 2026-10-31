import CaliBLE
import SwiftUI

/// The energy mode on the Energy tab (app.js FEATURES.energy `mode`): Normal / Max, and ECO only
/// when the unit reports a solar panel; locked while the unit says the mode is not selectable; the
/// web UI's "derived from the app" prompt before each write.
///
/// - Requirement: R_IOS_FUNCTIONS, R_IOS_I18N
struct EnergyModeView: View {
    let store: CamperStore

    /// `CTL_ENERGY_MODES` with the web UI's option labels.
    static let modes: [(key: String, value: Int, label: LocalizedStringResource)] = [
        ("normal", 0, "Normal"), ("max_charge", 1, "Max"), ("eco", 2, "ECO"),
    ]

    static func mode(_ key: String) -> Command { Command(function: "energy", what: "mode", value: key) }

    var body: some View {
        ControlSection(store: store, function: "energy") { run in
            let solar = store.value("energy", "solar_installed")?.bool == true
            let current = store.value("energy", "energy_mode")?.number.map(Int.init)
            let locked = store.value("energy", "energy_mode_locked")?.bool == true
            Card {
                ControlRow(disabledBecause: locked ? "The unit currently does not allow changing the energy mode" : nil) {
                    MenuPicker(label: Text("Energy mode"),
                               value: Self.modes.first { $0.value == current }.map { Text($0.label) } ?? Text(verbatim: "—"),
                               selection: Binding(get: { Self.modes.first { $0.value == current }?.key ?? "" }, set: {
                        run(Self.mode($0), confirm: ["Set the energy management mode? This control is derived from the app and not yet verified on the van. Continue?"])
                    })) {
                        if !Self.modes.contains(where: { $0.value == current }) { Text(verbatim: "—").tag("") }
                        ForEach(Self.modes.filter { $0.key != "eco" || solar }, id: \.key) { Text($0.label).tag($0.key) }
                    }
                }
            }
        }
        .tint(.onGreen)
    }
}

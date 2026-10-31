import CaliBLE
import SwiftUI

/// The cooler (app.js FEATURES.cooler): the refrigerator box switch, the cooling level 1–5 and the
/// quiet mode (only while the box is on, with the web UI's "not yet verified" prompt), the door and
/// the unit's fault text. The start timer and the quiet-mode hours are not in this app yet.
///
/// - Requirement: R_IOS_FUNCTIONS, R_IOS_I18N, R_IOS_HONEST
struct CoolerView: View {
    let store: CamperStore

    /// `CTL_COOLER_MODES` with the web UI's option labels.
    static let modes: [(key: String, value: Int, label: LocalizedStringResource)] = [
        ("normal", 0, "Off"), ("quiet", 2, "Manual"), ("timer_quiet", 4, "Automatic"),
    ]
    /// `COOLER_FAULT_MSG` without the ⚠ glyph.
    static let faultText: [String: LocalizedStringResource] = [
        "door_open": "Please close the refrigerator box door fully",
        "emergency": "Refrigerator box in emergency mode",
        "error": "Refrigerator box error — please visit a workshop",
    ]

    static func power(_ on: Bool) -> Command { .onOff("cooler", "power", on) }
    static func level(_ n: Int) -> Command { Command(function: "cooler", what: "level", value: String(n)) }
    static func mode(_ key: String) -> Command { Command(function: "cooler", what: "mode", value: key) }

    private func num(_ key: String) -> Int? { store.value("cooler", key)?.number.map(Int.init) }

    var body: some View {
        ControlPage(store: store, function: "cooler", title: "Cooler") { run in
            let on = store.value("cooler", "on")?.bool == true
            if let fault = store.value("cooler", "fault")?.string, let text = Self.faultText[fault] {
                AlertCard(text: text)
            }
            Card {
                ControlRow {
                    SwitchRow(label: Text("Refrigerator box"), isOn: Binding(get: { on }, set: { run(Self.power($0)) }))
                }
                ControlRow {
                    MenuPicker(
                        label: Text("Cooling level"), value: Text(verbatim: num("level").map(String.init) ?? "—"),
                        selection: Binding(get: { num("level") ?? 0 }, set: { run(Self.level($0)) })
                    ) {
                        if num("level").map({ !(1...5).contains($0) }) ?? true { Text(verbatim: "—").tag(num("level") ?? 0) }
                        ForEach(1...5, id: \.self) { Text(verbatim: "\($0)").tag($0) }
                    }
                }
                ControlRow(disabledBecause: on ? nil : "Switch the refrigerator box on first") {
                    MenuPicker(
                        label: Text("Quiet mode"), value: Text(Self.modes.first { $0.value == num("mode") }?.label ?? "Off"),
                        selection: Binding(
                            get: { Self.modes.first { $0.value == num("mode") }?.key ?? "normal" },
                            set: {
                                run(
                                    Self.mode($0),
                                    confirm: ["Set the refrigerator box's quiet mode? Not yet verified on the van. Continue?"])
                            })
                    ) {
                        ForEach(Self.modes, id: \.key) { Text($0.label).tag($0.key) }
                    }
                }
            }
            Card {
                Row("Refrigerator box door", Text(store.value("cooler", "door_open")?.bool == true ? "Open" : "Closed"))
            }
        }
    }
}

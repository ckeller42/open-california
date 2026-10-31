import CaliBLE
import SwiftUI

/// Camping mode: the master switch and, while it is on, the exterior/interior lighting and the rear
/// USB ports (app.js FEATURES.campingmode, same gates and words).
///
/// - Requirement: R_IOS_FUNCTIONS, R_IOS_I18N
struct CampingView: View {
    let store: CamperStore

    static func master(_ on: Bool) -> Command { .onOff("campingmode", "master", on) }
    static func lights(_ on: Bool) -> Command { .onOff("campingmode", "lights", on) }
    static func usb(_ on: Bool) -> Command { .onOff("campingmode", "usb", on) }

    private func on(_ key: String) -> Bool { store.value("campingmode", key)?.bool == true }

    /// The vehicle char's ignition, else campingmode `enable`, which mirrors it (app.js `ignitionOn`).
    private var ignitionOn: Bool { store.value("vehicle", "ignition_on")?.bool ?? on("enable") }

    var body: some View {
        ControlPage(store: store, function: "campingmode", title: "Camping mode") { run in
            let master = on("master_on")
            Card {
                ControlRow(disabledBecause: ignitionOn ? "Only possible when stationary" : nil) {
                    SwitchRow(label: Text("Camping mode"), isOn: Binding(get: { master }, set: { run(Self.master($0)) }))
                }
                ControlRow(disabledBecause: master ? nil : "Turn camping mode on first") {
                    SwitchRow(
                        label: Text("Exterior and interior lighting"),
                        isOn: Binding(get: { on("lights_on") }, set: { run(Self.lights($0)) }))
                }
                ControlRow(disabledBecause: master ? nil : "Turn camping mode on first") {
                    SwitchRow(
                        label: Text("Rear USB ports"), isOn: Binding(get: { on("usb_powered") }, set: { run(Self.usb($0)) }))
                }
            }
            Card {
                Row("Ignition", Text(ignitionOn ? "On" : "Off"))
            }
        }
    }
}

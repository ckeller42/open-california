import CaliBLE
import SwiftUI

/// The cooler (app.js FEATURES.cooler): the refrigerator box switch, the cooling level 1–5, the
/// quiet mode and its automatic schedule (all only while the box is on), the start timer (only while
/// the box is off), the door, the unit's fault text and the timer/schedule readouts. Every timer
/// control carries the web UI's "not yet verified on the van" prompt; the gates mirror the server's
/// (`command_precondition`), which is the real one.
///
/// - Requirement: R_IOS_FUNCTIONS, R_IOS_I18N, R_IOS_HONEST
struct CoolerView: View {
    let store: CamperStore
    @Environment(\.locale) private var locale

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
    static func nightOn(_ hour: Int) -> Command { Command(function: "cooler", what: "night_on", value: String(hour)) }
    static func nightOff(_ hour: Int) -> Command { Command(function: "cooler", what: "night_off", value: String(hour)) }
    static func timerSet(_ hhmm: String) -> Command { Command(function: "cooler", what: "timer_set", value: hhmm) }
    static let timerStart = Command(function: "cooler", what: "timer_start", value: nil)
    static let timerCancel = Command(function: "cooler", what: "timer_cancel", value: nil)

    private func num(_ key: String) -> Int? { store.value("cooler", key)?.number.map(Int.init) }

    /// The configured cooling-start time `HH:MM` (semantics `timer_hour`/`timer_min`), or nil.
    private var timerSetTime: String? {
        guard let h = num("timer_hour"), let m = num("timer_min") else { return nil }
        return String(format: "%02d:%02d", h, m)
    }

    /// The web UI's `{b}? …` prompt: the button's own wording, then the "not verified" question.
    private func notVerified(_ action: LocalizedStringResource) -> LocalizedStringResource {
        "\(action.string(locale))? Not yet verified on the van. Continue?"
    }

    /// The automatic quiet-mode schedule readout (app.js cooler `readouts`): `HH:00 → HH:00`, with
    /// " (off)" when the schedule is set but not active; "off" when no schedule is reported. The hours
    /// show only when both are known — never a fabricated 00:00 (`R_IOS_HONEST`).
    private var quietSchedule: Text {
        let scheduled = store.value("cooler", "quiet_scheduled")?.bool == true
        if let from = num("quiet_from"), let to = num("quiet_to"), scheduled || from != 0 || to != 0 {
            let span = String(format: "%02d:00 → %02d:00", from, to)
            return scheduled ? Text(verbatim: span) : Text(verbatim: span + " (") + Text("off") + Text(verbatim: ")")
        }
        return Text("off")
    }

    var body: some View {
        ControlPage(store: store, function: "cooler", title: "Cooler") { run in
            let on = store.value("cooler", "on")?.bool == true
            if let fault = store.value("cooler", "fault")?.string, let text = Self.faultText[fault] {
                AlertCard(text: text)
            }
            Card {
                ControlRow {
                    SwitchRow(
                        id: "cooler.power", label: Text("Refrigerator box"),
                        isOn: Binding(get: { on }, set: { run(Self.power($0)) }))
                }
                ControlRow {
                    SliderRow(
                        id: "cooler.level", label: Text("Cooling level"), range: 1...5, value: num("level"),
                        text: { (1...5).contains($0) ? Text(verbatim: "\($0)") : nil }, send: { run(Self.level($0)) })
                }
                ControlRow(disabledBecause: on ? nil : "Switch the refrigerator box on first") {
                    MenuPicker(
                        id: "cooler.quietMode", label: Text("Quiet mode"),
                        value: Text(Self.modes.first { $0.value == num("mode") }?.label ?? "Off"),
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
                ControlRow(disabledBecause: on ? nil : "Switch the refrigerator box on first") {
                    MenuPicker(
                        id: "cooler.nightOn", label: Text("Quiet mode starts at"),
                        value: Text(verbatim: num("quiet_from").map { String(format: "%02d:00", $0) } ?? "—"),
                        selection: Binding(
                            get: { num("quiet_from") ?? 0 },
                            set: { h in
                                run(
                                    Self.nightOn(h),
                                    confirm: [
                                        // swiftlint:disable:next line_length - a localization key stays one literal
                                        "Set the automatic quiet mode start to \(String(format: "%02d", h)):00? Not verified on the van. Continue?"
                                    ])
                            })
                    ) { ForEach(0..<24, id: \.self) { Text(verbatim: String(format: "%02d:00", $0)) } }
                }
                ControlRow(disabledBecause: on ? nil : "Switch the refrigerator box on first") {
                    MenuPicker(
                        id: "cooler.nightOff", label: Text("Quiet mode ends at"),
                        value: Text(verbatim: num("quiet_to").map { String(format: "%02d:00", $0) } ?? "—"),
                        selection: Binding(
                            get: { num("quiet_to") ?? 0 },
                            set: { h in
                                run(
                                    Self.nightOff(h),
                                    confirm: [
                                        // swiftlint:disable:next line_length - a localization key stays one literal
                                        "Set the automatic quiet mode end to \(String(format: "%02d", h)):00? Not verified on the van. Continue?"
                                    ])
                            })
                    ) { ForEach(0..<24, id: \.self) { Text(verbatim: String(format: "%02d:00", $0)) } }
                }
            }
            Card {
                ControlRow(disabledBecause: on ? "Switch the refrigerator box off first" : nil) {
                    TimeRow(
                        id: "cooler.timerSet", label: Text("Cooling starts at"), current: timerSetTime,
                        send: { t in
                            run(
                                Self.timerSet(t),
                                confirm: [
                                    "Set the timer so cooling starts at \(t)? Not yet verified on the van. Continue?"
                                ])
                        })
                }
                ControlRow(disabledBecause: on ? "Switch the refrigerator box off first" : nil) {
                    TimerButtons(label: Text("Timer")) {
                        Button("Start timer") { run(Self.timerStart, confirm: [notVerified("Start timer")]) }
                            .accessibilityIdentifier("cooler.timerStart")
                        Button("Cancel timer") { run(Self.timerCancel, confirm: [notVerified("Cancel timer")]) }
                            .accessibilityIdentifier("cooler.timerCancel")
                    }
                }
            }
            Card {
                Row("Refrigerator box door", Text(store.value("cooler", "door_open")?.bool == true ? "Open" : "Closed"))
                Row("Timer", Text(store.value("cooler", "timer_active")?.bool == true ? "On" : "Off"))
                Row("Automatic quiet mode", quietSchedule)
            }
        }
    }
}

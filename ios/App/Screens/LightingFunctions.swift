import CaliBLE
import CaliCore
import SwiftUI

/// The unit-reported wake-up light config (`semantics.wakeup_config`, the `lighting.wakeup` object).
/// The unit reports it only in a Mode-20 frame; nil until one was seen.
struct WakeUp: Equatable {
    var time: String
    var enabled: Bool
    var ramp: Int
    var brightness: Int
    var areas: [Int]

    init(time: String, enabled: Bool, ramp: Int, brightness: Int, areas: [Int]) {
        (self.time, self.enabled, self.ramp, self.brightness, self.areas) = (time, enabled, ramp, brightness, areas)
    }

    init?(_ v: JSONValue?) {
        guard let v, let time = v["time"]?.string, let enabled = v["enabled"]?.bool,
            let ramp = v["ramp"]?.number, let brightness = v["brightness"]?.number,
            case .array(let a)? = v["areas"]
        else { return nil }
        self.init(
            time: time, enabled: enabled, ramp: Int(ramp), brightness: Int(brightness),
            areas: a.compactMap { $0.number.map(Int.init) })
    }
}

/// The web UI's "Functions & settings" cards of the lighting page (`renderLighting`): the wake-up
/// light (the app's `dg/h.m0`, Mode 20) and "Lighting & sliding door" (`dg/h.n4`, SET_PROFILE PN 8).
/// Every wake-up change resends the whole config like the app; the time never flips the switch.
/// Nothing is invented: with no config reported, only the time is live, and it goes alone, so the
/// unit's config is pulled first (R5) and the rest comes from what the unit reports.
///
/// - Requirement: R_IOS_FUNCTIONS, R_IOS_HONEST, R_IOS_I18N
struct LightingFunctions: View {
    let store: CamperStore
    let run: Run

    /// The app's T7 vehicle areas A1–A4 (`ti/b.java:14-28`), the web UI's `WAKE_AREAS`.
    static let areas: [LocalizedStringResource] = [
        "Living area reading lights", "Kitchen background lighting", "Pop-up roof reading lights",
        "Pop-up roof background lighting",
    ]
    static let leadTimes = [0, 10, 20, 30]

    /// `lighting wakeup "HH:MM areas brightness ramp [on|off]"` over the reported config.
    static func wakeup(_ w: WakeUp, on: Bool? = nil) -> Command {
        let base = "\(w.time) \(w.areas.map(String.init).joined(separator: ",")) \(w.brightness) \(w.ramp)"
        return Command(function: "lighting", what: "wakeup", value: on.map { base + ($0 ? " on" : " off") } ?? base)
    }
    /// With no config reported: the time alone (the planner pulls the unit's config first).
    static func wakeupTime(_ time: String) -> Command { Command(function: "lighting", what: "wakeup", value: time) }
    static func doorContact(_ on: Bool) -> Command { .onOff("lighting", "door_contact", on) }

    @State private var picking: Date?

    var body: some View {
        let wk = WakeUp(store.value("lighting", "wakeup"))
        Card {
            Text("Wake-up light").font(.headline).padding([.horizontal, .top])
            Group {
                ControlRow {
                    SwitchRow(
                        id: "lighting.wakeup", label: Text("Wake-up light"),
                        isOn: Binding(get: { wk?.enabled == true }, set: { on in wk.map { run(Self.wakeup($0, on: on)) } }))
                }
                .disabled(wk == nil)
                ControlRow {
                    StackedRow(label: Text("Wake-up time")) {
                        Button {
                            picking = Self.date(wk?.time ?? "07:00")
                        } label: {
                            // At least 44 × 44 pt to tap, also when it reads only "—" (HIG hit area).
                            Text(verbatim: wk?.time ?? "—").monospacedDigit()
                                .frame(minWidth: 44, minHeight: 44, alignment: .trailing)
                                .contentShape(Rectangle())
                        }
                        .accessibilityLabel(Text("Wake-up time"))
                        .accessibilityValue(Text(verbatim: wk?.time ?? "—"))
                        .accessibilityIdentifier("lighting.wakeup.time")
                    }
                }
                Group {
                    ControlRow {
                        MenuPicker(
                            id: "lighting.wakeup.leadTime", label: Text("Lead time"),
                            value: Text("\(wk?.ramp ?? 0) min"),
                            selection: Binding(get: { wk?.ramp ?? 0 }, set: { m in edit(wk) { $0.ramp = m } })
                        ) { ForEach(Self.leadTimes, id: \.self) { Text("\($0) min").tag($0) } }
                    }
                    ControlRow {
                        SliderRow(
                            id: "lighting.wakeup.brightness", label: Text("Brightness"), spoken: Text("Wake-up brightness"),
                            range: 0...10, value: wk?.brightness, text: { (0...10).contains($0) ? Text(verbatim: "\($0)") : nil },
                            send: { b in edit(wk) { $0.brightness = b } })
                    }
                    Text("Vehicle area").font(.subheadline.weight(.semibold)).padding([.horizontal, .top])
                    ForEach(1...4, id: \.self) { a in
                        let on = wk?.areas.contains(a) == true
                        // Never the last area off: the unit needs one (the app's "no area" dialog).
                        let last = on && wk?.areas.count == 1
                        ControlRow(disabledBecause: last ? "The wake-up light needs at least one area" : nil) {
                            SwitchRow(
                                id: "lighting.wakeup.area\(a)", label: Text(Self.areas[a - 1]),
                                isOn: Binding(
                                    get: { on },
                                    set: { v in
                                        edit(wk) { $0.areas = v ? ($0.areas + [a]).sorted() : $0.areas.filter { $0 != a } }
                                    }))
                        }
                    }
                }
                .disabled(wk == nil)
            }
            if wk == nil {
                Text("Wake-up settings not known yet — the unit has not reported them")
                    .font(.footnote).padding([.horizontal, .bottom])
            }
        }
        .sheet(isPresented: Binding(get: { picking != nil }, set: { if !$0 { picking = nil } })) {
            timeSheet(wk)
        }
        // The app hides the sliding-door row only on a Grand California (raw CarVariant 1).
        if store.value("vehicle", "car_variant")?.number != 1 {
            Card {
                Text("Lighting & sliding door").font(.headline).padding([.horizontal, .top])
                ControlRow {
                    SwitchRow(
                        id: "lighting.doorContact", label: Text("Opening sliding door activates the rear interior lights."),
                        isOn: Binding(
                            get: { store.value("lighting", "door_contact")?.bool == true },
                            set: { run(Self.doorContact($0)) }))
                }
            }
        }
    }

    /// Sends the reported config with one change (the controls are disabled without one).
    private func edit(_ wk: WakeUp?, _ change: (inout WakeUp) -> Void) {
        guard var w = wk else { return }
        change(&w)
        run(Self.wakeup(w))
    }

    /// The app's time drawer: a wheel and OK; the time goes out only on OK.
    private func timeSheet(_ wk: WakeUp?) -> some View {
        NavigationStack {
            DatePicker(
                "Wake-up time",
                selection: Binding(get: { picking ?? .now }, set: { picking = $0 }),
                displayedComponents: .hourAndMinute
            )
            .datePickerStyle(.wheel)
            .labelsHidden()
            .navigationTitle(Text("Wake-up time"))
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .cancellationAction) { Button("Cancel") { picking = nil } }
                ToolbarItem(placement: .confirmationAction) {
                    Button("OK") {
                        if let d = picking {
                            let t = Self.hhmm(d)
                            if var w = wk {
                                w.time = t
                                run(Self.wakeup(w))
                            } else {
                                run(Self.wakeupTime(t))
                            }
                        }
                        picking = nil
                    }
                    .accessibilityIdentifier("lighting.wakeup.timeOK")
                }
            }
        }
        .presentationDetents([.medium])
    }

    /// "HH:MM" in the phone's wall clock (the planner packs it with the phone's local time).
    static func hhmm(_ d: Date) -> String {
        let c = Calendar.current.dateComponents([.hour, .minute], from: d)
        return String(format: "%02d:%02d", c.hour ?? 0, c.minute ?? 0)
    }
    static func date(_ hhmm: String) -> Date {
        let p = hhmm.split(separator: ":").compactMap { Int($0) }
        return Calendar.current.date(bySettingHour: p.first ?? 0, minute: p.count > 1 ? p[1] : 0, second: 0, of: .now) ?? .now
    }
}

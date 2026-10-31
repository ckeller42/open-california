import CaliBLE
import SwiftUI

/// Lighting like the web UI's `renderLighting`: "All lights", the favourite tiles A–D and the lamps
/// grouped like the app, each a brightness 0–10. A favourite the unit reports empty cannot be
/// recalled. Only the `1502` notification confirms a lighting command; else "Sent — check the lamp".
///
/// - Requirement: R_IOS_FUNCTIONS, R_IOS_HONEST, R_IOS_I18N
struct LightsView: View {
    let store: CamperStore

    /// The web UI's `FAV_TILES`: the app's favourite slots A/B/C/D are the unit's profiles 1/5/6/7.
    static let favourites: [(label: String, profile: Int)] = [("A", 1), ("B", 5), ("C", 6), ("D", 7)]

    struct Lamp { let label: LocalizedStringResource; let what: String; let zone: Int }
    /// The web UI's `LIGHT_LAMPS`: `what` is the control zone key, `zone` the interpreted brightness_zone_<N>.
    static let groups: [(title: LocalizedStringResource, lamps: [Lamp])] = [
        ("Reading lights", [Lamp(label: "Left", what: "reading-1", zone: 2), Lamp(label: "Right", what: "reading-2", zone: 1),
                            Lamp(label: "Front passenger", what: "reading-3", zone: 4)]),
        ("Kitchen", [Lamp(label: "Background lighting", what: "kitchen-ambient", zone: 5),
                     Lamp(label: "Cabinet", what: "kitchen-cabinet", zone: 6), Lamp(label: "Cooking", what: "kitchen", zone: 7)]),
        ("Pop-up roof", [Lamp(label: "Background lighting", what: "roof-ambient", zone: 8),
                         Lamp(label: "Reading light", what: "roof-reading", zone: 9)]),
        ("Exterior light", [Lamp(label: "Rear surroundings", what: "outside-rear", zone: 3), Lamp(label: "Entrance", what: "entrance", zone: 12)]),
    ]

    static func allLights(_ on: Bool) -> Command { .onOff("lighting", "power", on) }
    static func favourite(_ profile: Int) -> Command { Command(function: "lighting", what: "profile", value: String(profile)) }
    static func lamp(_ what: String, _ level: Int) -> Command { Command(function: "lighting", what: what, value: String(level)) }

    /// The roof reading light has no power while the pop-up roof is down (unknown position: allowed).
    private var roofDown: Bool {
        guard let pos = store.value("roof", "position_name")?.string else { return false }
        return pos != "open" && pos != "middle"
    }

    var body: some View {
        ControlPage(store: store, function: "lighting", title: "Lighting", tint: .lightAmber) { run in
            let stored = store.value("lighting", "favourites_stored").flatMap { v -> [Int]? in
                if case .array(let a) = v { a.compactMap { $0.number.map(Int.init) } } else { nil }
            }
            Card {
                ControlRow {
                    SwitchRow(label: Text("All lights"), isOn: Binding(get: { store.value("lighting", "any_on")?.bool == true },
                                                       set: { run(Self.allLights($0)) }))
                }
                let active = store.value("lighting", "profile")?.number.map(Int.init)
                HStack(spacing: 12) {
                    ForEach(Self.favourites, id: \.profile) { fav in
                        // A slot the unit reports empty cannot be recalled (saving is not in this app).
                        let empty = stored.map { !$0.contains(fav.profile) } ?? false
                        let isActive = active == fav.profile
                        Button { run(Self.favourite(fav.profile)) } label: {
                            VStack(spacing: 0) {
                                Text(verbatim: fav.label).font(.title3.weight(.semibold))
                                if empty { Text("empty").font(.caption2) }
                            }
                            .foregroundStyle(empty ? Color.staleText : Color.primary)
                            .frame(maxWidth: .infinity, minHeight: 52)
                            .background(Color.lightAmber.opacity(isActive ? 0.4 : empty ? 0.04 : 0.15), in: RoundedRectangle(cornerRadius: 12))
                            .overlay { if isActive { RoundedRectangle(cornerRadius: 12).stroke(Color.lightAmber, lineWidth: 2) } }
                        }
                        .buttonStyle(.plain)
                        .disabled(empty)
                        .accessibilityLabel(Text("Favourite \(fav.label)"))
                        .accessibilityValue(isActive ? Text("active") : empty ? Text("empty") : Text(verbatim: ""))
                        .accessibilityHint(empty ? Text("This favourite is empty on the unit") : Text(verbatim: ""))
                    }
                }
                .padding([.horizontal, .bottom])
            }
            ForEach(Self.groups.indices, id: \.self) { g in
                let group = Self.groups[g]
                Card {
                    Text(group.title).font(.headline).padding([.horizontal, .top])
                    ForEach(group.lamps, id: \.what) { lamp in
                        let blocked = lamp.what == "roof-reading" && roofDown
                        ControlRow(disabledBecause: blocked ? "only when the pop-up roof is open" : nil,
                                   hint: lamp.what == "roof-reading" && !blocked ? "only when the pop-up roof is open" : nil) {
                            brightness(lamp, group: group.title, run)
                        }
                    }
                }
            }
        }
    }

    /// A lamp's brightness picker: Off, 1–10; the unit's 11 (its default level) shows as "Default".
    /// The no-reading markers (13, 14) select nothing.
    private func brightness(_ lamp: Lamp, group: LocalizedStringResource, _ run: Run) -> some View {
        let real = store.value("lighting", "brightness_zone_\(lamp.zone)")?.number.map(Int.init)
        let current = real.flatMap { $0 <= 11 ? $0 : nil }
        // Two lamps share a name ("Background lighting"): VoiceOver hears the group too.
        return MenuPicker(label: Text(lamp.label), spoken: Text(group) + Text(verbatim: ", ") + Text(lamp.label),
                          value: current == 0 ? Text("Off") : current == 11 ? Text("Default") : Text(verbatim: current.map(String.init) ?? "—"),
                          selection: Binding(get: { current ?? -1 }, set: { if (0...10).contains($0) { run(Self.lamp(lamp.what, $0)) } })) {
            if current == nil { Text(verbatim: "—").tag(-1) }
            Text("Off").tag(0)
            ForEach(1...10, id: \.self) { Text(verbatim: "\($0)").tag($0) }
            if current == 11 { Text("Default").tag(11) }
        }
    }
}

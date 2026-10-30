import CaliCore
import SwiftUI

/// The tabs: Cockpit / Energy / Water / Setup.
///
/// - Requirement: R_IOS_LOOK, R_IOS_I18N
struct RootView: View {
    let store: CamperStore

    var body: some View {
        TabView {
            NavigationStack { CockpitView(store: store).navigationTitle("Cockpit").navigationBarTitleDisplayMode(.inline) }
                .tabItem { Label("Cockpit", systemImage: "gauge.with.dots.needle.67percent") }
            NavigationStack { EnergyView(store: store).navigationTitle("Energy").navigationBarTitleDisplayMode(.inline) }
                .tabItem { Label("Energy", systemImage: "bolt.batteryblock") }
            NavigationStack { WaterView(store: store).navigationTitle("Water").navigationBarTitleDisplayMode(.inline) }
                .tabItem { Label("Water", systemImage: "drop") }
            NavigationStack { StatusView(store: store).navigationTitle("Setup").navigationBarTitleDisplayMode(.inline) }
                .tabItem { Label("Setup", systemImage: "gearshape") }
        }
    }
}

/// The start screen: the second-battery ring, the small values and the large function buttons.
/// Each value and button appears only when the unit reports its function installed. There is no
/// outside temperature: the unit reports none (the web UI's comes from a satellite sensor).
///
/// - Requirement: R_IOS_LOOK, R_IOS_INSTALLED, R_IOS_FUNCTIONS
struct CockpitView: View {
    let store: CamperStore
    @Environment(\.dynamicTypeSize) private var typeSize
    @Environment(\.locale) private var locale

    var body: some View {
        StatusPage(store: store) {
            if store.installed("energy") {
                BatteryRing(percent: store.value("energy", "soc2_pct")?.number,
                            volts: store.value("energy", "batt2_v")?.number,
                            amps: store.value("energy", "batt2_current")?.number,
                            asOf: store.state.asOf)
            }
            HStack(spacing: 12) {
                if store.installed("water"), let liters = store.value("water", "fresh")?["liters"]?.number {
                    MiniValue(title: "Fresh water", systemImage: "drop", value: "\(Int(liters))\(nbsp)L",
                              spoken: spoken(liters, UnitVolume.liters, locale),
                              heldSince: store.state.waterHeld ? .some(store.state.waterGoodAt) : nil)
                }
                if store.value("energy", "solar_installed")?.bool == true,
                   let watts = store.value("energy", "solar_power")?.number {
                    MiniValue(title: "Solar power", systemImage: "sun.max", value: "\(Int(watts))\(nbsp)W",
                              spoken: spoken(watts, UnitPower.watts, locale), heldSince: nil)
                }
            }
            // ponytail: the buttons open nothing yet; the control screens (camping, lighting, cooler, air heater) wire them.
            // Two per row; one per row at large type sizes, where a long word (Luftstandheizung) needs the width.
            let tiles = buttons, perRow = typeSize >= .xxLarge ? 1 : 2
            Grid(horizontalSpacing: 12, verticalSpacing: 12) {
                ForEach(Array(stride(from: 0, to: tiles.count, by: perRow)), id: \.self) { i in
                    GridRow {
                        ForEach(i..<min(i + perRow, tiles.count), id: \.self) { tiles[$0] }
                        if perRow == 2 && i + 1 == tiles.count { Color.clear.gridCellUnsizedAxes([.horizontal, .vertical]) }
                    }
                }
            }
        }
    }

    private func on(_ function: String, _ key: String) -> Bool { store.value(function, key)?.bool == true }

    /// Why `function`'s button is disabled, for VoiceOver; nil when it is enabled.
    private func locked(_ function: String) -> LocalizedStringResource? {
        if store.controlEnabled(function) { return nil }
        if case .unsupported = store.state.gate { return "This camper unit is not supported yet" }
        if store.link != .up || store.isStale { return "Not available while the van is offline" }
        return "Waiting for the camper unit's data"
    }

    /// The function buttons of the installed functions, in the mockup's order.
    private var buttons: [ControlButton] {
        var b: [ControlButton] = []
        if store.installed("campingmode") {
            b.append(ControlButton(title: "Camping mode", systemImage: "tent", isOn: on("campingmode", "master_on"),
                                   tint: .onGreen, lockedBecause: locked("campingmode")) {})
        }
        if store.installed("lighting") {
            b.append(ControlButton(title: "Lighting", systemImage: "lightbulb", isOn: on("lighting", "any_on"),
                                   tint: .lightAmber, lockedBecause: locked("lighting")) {})
        }
        if store.installed("cooler") {
            b.append(ControlButton(title: "Cooler", systemImage: "snowflake", isOn: on("cooler", "on"),
                                   tint: .onGreen, lockedBecause: locked("cooler")) {})
        }
        if store.installed("airheater") {
            b.append(ControlButton(title: "Air heater", systemImage: "heater.vertical", isOn: on("airheater", "running"),
                                   tint: .onGreen, lockedBecause: locked("airheater")) {})
        }
        return b
    }
}

/// One of the cockpit's small values: an SF Symbol, a label, the value and, for a held reading, its
/// measurement age (from the page's clock, so it grows on an open screen). Stale data draws muted.
///
/// - Requirement: R_IOS_LOOK, R_IOS_HONEST
struct MiniValue: View {
    let title: LocalizedStringResource
    let systemImage: String
    let value: String
    /// The value in full words for VoiceOver.
    let spoken: String
    /// `.some(date)`: a held reading, measured at `date` (nil: unknown); nil: a live reading.
    let heldSince: Date??
    @Environment(\.pageStale) private var stale
    @Environment(\.pageNow) private var pageNow
    @Environment(\.locale) private var locale

    var body: some View {
        VStack(alignment: .leading, spacing: 4) {
            Label { Text(title) } icon: { Image(systemName: systemImage) }
                .font(.caption)
            Text(verbatim: value)
                .font(.title2.weight(.semibold).monospacedDigit())
                .foregroundStyle(stale ? Color.staleText : Color.primary)
            if let heldSince {
                HeldNote(since: heldSince)
                    .font(.caption)
            }
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .padding(12)
        .background(Color(uiColor: .secondarySystemBackground), in: RoundedRectangle(cornerRadius: 16))
        .accessibilityElement(children: .ignore)
        .accessibilityLabel(Text(title))
        .accessibilityValue(Text(verbatim: heldSince.map {
            "\(spoken), " + HeldNote.text(since: $0, now: pageNow ?? Date(), locale: locale, spoken: true)
        } ?? spoken))
    }
}

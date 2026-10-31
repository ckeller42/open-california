import CaliCore
import SwiftUI

/// The tabs: Cockpit / Energy / Water / Setup.
///
/// - Requirement: R_IOS_LOOK, R_IOS_I18N
struct RootView: View {
    let store: CamperStore
    @Environment(\.locale) private var locale

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
        // VoiceOver hears the sending state, the result and the connect outcome where it is.
        .onChange(of: store.feedback) { _, f in
            if let f, let text = FeedbackBanner.text(f, now: store.now()) {
                AccessibilityNotification.Announcement(text.string(locale)).post()
            }
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
    @State private var open: ControlScreen?
    @Environment(\.dynamicTypeSize) private var typeSize
    @Environment(\.locale) private var locale

    /// What a tap on a function button does: like the web UI's tiles (`goto(fn)`) it opens the
    /// function's screen in every state (a locked screen says why) and never sends a command; only
    /// in the power-save rest it connects again.
    ///
    /// - Requirement: R_IOS_SIRI_NO_HEATER, R_IOS_POWER_SAVE
    static func tap(_ screen: ControlScreen, store: CamperStore) -> CockpitTap {
        store.resting && store.lockReason(screen.function) != nil ? .connect : .open(screen)
    }

    var body: some View {
        StatusPage(store: store) {
            if store.installed("energy") {
                BatteryRing(percent: store.value("energy", "soc2_pct")?.number,
                            volts: store.value("energy", "batt2_v")?.number,
                            amps: store.value("energy", "batt2_current")?.number,
                            asOf: store.state.asOf)
            }
            // Active faults, in the same alert cards as on the Energy and Water tabs.
            let alerts = EnergyView.faults(store) + (store.installed("water") ? WaterView.alerts(store) : [])
            ForEach(alerts.indices, id: \.self) { AlertCard(text: alerts[$0]) }
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
        .navigationDestination(item: $open) { $0.view(store) }
    }

    private func on(_ function: String, _ key: String) -> Bool { store.value(function, key)?.bool == true }

    /// The function buttons of the installed functions, in the mockup's order.
    private var buttons: [ControlButton] {
        let all: [(ControlScreen, LocalizedStringResource, String, String, Color)] = [
            (.camping, "Camping mode", "tent", "master_on", .onGreen),
            (.lighting, "Lighting", "lightbulb", "any_on", .lightAmber),
            (.cooler, "Cooler", "snowflake", "on", .onGreen),
            (.airHeater, "Air heater", "heater.vertical", "running", .onGreen),
        ]
        return all.filter { store.installed($0.0.function) }.map { screen, title, symbol, key, tint in
            ControlButton(title: title, systemImage: symbol, isOn: on(screen.function, key), tint: tint,
                          lockedBecause: store.lockReason(screen.function)) {
                switch Self.tap(screen, store: store) {
                case .open(let s): open = s
                case .connect: Task { await store.tapToConnect() }
                }
            }
        }
    }
}

/// The control screens the cockpit buttons open.
enum ControlScreen: Hashable, Identifiable {
    case camping, lighting, cooler, airHeater

    var id: Self { self }

    var function: String {
        switch self {
        case .camping: "campingmode"
        case .lighting: "lighting"
        case .cooler: "cooler"
        case .airHeater: "airheater"
        }
    }

    @MainActor @ViewBuilder func view(_ store: CamperStore) -> some View {
        switch self {
        case .camping: CampingView(store: store)
        case .lighting: LightsView(store: store)
        case .cooler: CoolerView(store: store)
        case .airHeater: AirHeaterView(store: store)
        }
    }
}

/// A cockpit tap: open a control screen, or connect again after the power-save rest.
enum CockpitTap: Equatable {
    case open(ControlScreen)
    case connect
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
    @Environment(\.pageStaleSpoken) private var staleSpoken
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
        } ?? spoken) + Text(verbatim: staleSpoken.map { ", \($0)" } ?? ""))
    }
}

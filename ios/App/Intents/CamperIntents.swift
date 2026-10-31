import AppIntents
import CaliBLE

// The Siri and Shortcuts actions. Each builds its command with the control screen's builder (one
// mapping). There is no air-heater action (R_IOS_SIRI_NO_HEATER): the burner is switched only by a
// tap on its own screen.

/// On or off, as Siri hears and says it.
enum OnOff: String, AppEnum {
    case on, off

    static let typeDisplayRepresentation: TypeDisplayRepresentation = "State"
    static let caseDisplayRepresentations: [OnOff: DisplayRepresentation] = [.on: "on", .off: "off"]
}

/// The light favourites A–D of the web UI's tiles (unit profiles 1/5/6/7, `LightsView.favourites`);
/// "1"–"4" are spoken synonyms.
///
/// - Requirement: R_IOS_SIRI
enum LightFavourite: String, AppEnum, CaseIterable {
    case a = "A", b = "B", c = "C", d = "D"

    static let typeDisplayRepresentation: TypeDisplayRepresentation = "Light favourite"
    static let caseDisplayRepresentations: [LightFavourite: DisplayRepresentation] = [
        .a: DisplayRepresentation(title: "A", synonyms: ["1"]),
        .b: DisplayRepresentation(title: "B", synonyms: ["2"]),
        .c: DisplayRepresentation(title: "C", synonyms: ["3"]),
        .d: DisplayRepresentation(title: "D", synonyms: ["4"]),
    ]

    /// The unit's profile number of this tile.
    @MainActor var profile: Int { LightsView.favourites.first { $0.label == rawValue }!.profile }
}

/// Camping mode on or off: `campingmode master on|off`.
///
/// - Requirement: R_IOS_SIRI, R_IOS_SIRI_HONEST
struct SetCampingModeIntent: AppIntent {
    static let title: LocalizedStringResource = "Switch Camping Mode"
    static let description = IntentDescription("Turns the camping mode of the camper unit on or off.")

    @Parameter(title: "State") var state: OnOff

    init() {}
    init(on: Bool) { self.init(); state = on ? .on : .off }

    @MainActor var command: Command { CampingView.master(state == .on) }

    static var parameterSummary: some ParameterSummary { Summary("Turn camping mode \(\.$state)") }

    @MainActor func perform() async throws -> some IntentResult & ProvidesDialog { await IntentRunner.run(command) }
}

/// All lights on or off: `lighting power on|off` (the web UI's "All lights"; `lighting all` takes a
/// brightness).
///
/// - Requirement: R_IOS_SIRI, R_IOS_SIRI_HONEST
struct SetLightsIntent: AppIntent {
    static let title: LocalizedStringResource = "Switch All Lights"
    static let description = IntentDescription("Turns all lights of the camper on or off.")

    @Parameter(title: "State") var state: OnOff

    init() {}
    init(on: Bool) { self.init(); state = on ? .on : .off }

    @MainActor var command: Command { LightsView.allLights(state == .on) }

    static var parameterSummary: some ParameterSummary { Summary("Turn all lights \(\.$state)") }

    @MainActor func perform() async throws -> some IntentResult & ProvidesDialog { await IntentRunner.run(command) }
}

/// A light favourite A–D: `lighting profile 1|5|6|7`.
///
/// - Requirement: R_IOS_SIRI, R_IOS_SIRI_HONEST
struct ActivateLightFavouriteIntent: AppIntent {
    static let title: LocalizedStringResource = "Recall Light Favourite"
    static let description = IntentDescription("Switches the lights to a favourite stored on the camper unit.")

    @Parameter(title: "Light favourite") var favourite: LightFavourite

    init() {}
    init(favourite: LightFavourite) { self.init(); self.favourite = favourite }

    @MainActor var command: Command { LightsView.favourite(favourite.profile) }

    static var parameterSummary: some ParameterSummary { Summary("Recall light favourite \(\.$favourite)") }

    @MainActor func perform() async throws -> some IntentResult & ProvidesDialog { await IntentRunner.run(command) }
}

/// The cooler on or off: `cooler power on|off`.
///
/// - Requirement: R_IOS_SIRI, R_IOS_SIRI_HONEST
struct SetCoolerIntent: AppIntent {
    static let title: LocalizedStringResource = "Switch Cooler"
    static let description = IntentDescription("Turns the cooler on or off.")

    @Parameter(title: "State") var state: OnOff

    init() {}
    init(on: Bool) { self.init(); state = on ? .on : .off }

    @MainActor var command: Command { CoolerView.power(state == .on) }

    static var parameterSummary: some ParameterSummary { Summary("Turn the cooler \(\.$state)") }

    @MainActor func perform() async throws -> some IntentResult & ProvidesDialog { await IntentRunner.run(command) }
}

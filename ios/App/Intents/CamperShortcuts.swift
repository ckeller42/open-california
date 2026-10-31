import AppIntents

/// The Siri phrases. Each contains the app's name (Apple's rule); the German phrases are the
/// translations of these keys in `AppShortcuts.xcstrings`.
///
/// - Requirement: R_IOS_SIRI, R_IOS_SIRI_NO_HEATER, R_IOS_I18N
struct CamperShortcuts: AppShortcutsProvider {
    static var appShortcuts: [AppShortcut] {
        AppShortcut(
            intent: SetCampingModeIntent(),
            phrases: [
                "Turn camping mode \(\.$state) in \(.applicationName)",
                "\(.applicationName) camping mode \(\.$state)",
            ], shortTitle: "Camping mode", systemImageName: "tent")
        AppShortcut(
            intent: SetLightsIntent(),
            phrases: [
                "Turn the lights \(\.$state) in \(.applicationName)",
                "\(.applicationName) lights \(\.$state)",
            ], shortTitle: "All lights", systemImageName: "lightbulb")
        AppShortcut(
            intent: ActivateLightFavouriteIntent(),
            phrases: [
                "Light favourite \(\.$favourite) in \(.applicationName)",
                "\(.applicationName) favourite \(\.$favourite)",
            ], shortTitle: "Light favourite", systemImageName: "star")
        AppShortcut(
            intent: SetCoolerIntent(),
            phrases: [
                "Turn the cooler \(\.$state) in \(.applicationName)",
                "\(.applicationName) cooler \(\.$state)",
            ], shortTitle: "Cooler", systemImageName: "snowflake")
    }
}

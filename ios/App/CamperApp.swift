import CaliBLE
import SwiftUI

extension CamperStore {
    /// The fixture of `-UITestFixture <name>`; nil in normal use.
    static let fixture: Fixture? = {
        let args = ProcessInfo.processInfo.arguments
        guard let i = args.firstIndex(of: "-UITestFixture"), i + 1 < args.count else { return nil }
        return Fixture(rawValue: args[i + 1])
    }()

    /// The app's one store: the scene and the Siri intents share it (iOS may launch the app for an
    /// intent without a scene). Under a fixture it runs over the fake transport.
    static let shared: CamperStore = {
        if let fixture { return .preview(fixture) }
        // The transport's default restore identifier lets iOS relaunch the app for the held link (camping on).
        let transport = CoreBluetoothTransport()
        // The last state, with its age, until this link has read the unit (never under a fixture).
        let url = defaultSaveURL
        return CamperStore(
            session: CamperSession(transport: transport, clock: ContinuousClock()),
            state: restore(from: url) ?? CamperState(), saveURL: url)
    }()
}

/// The app: the real Bluetooth transport, or under `-UITestFixture <name>` the fake transport with
/// that fixture (UI tests never use real Bluetooth).
///
/// - Requirement: R_IOS_BLE_DIRECT, R_IOS_BACKGROUND_LINK, R_IOS_TESTED
@main
struct CamperApp: App {
    @State private var store = CamperStore.shared
    @Environment(\.scenePhase) private var scenePhase
    private let live = CamperStore.fixture?.isLive ?? true

    var body: some Scene {
        WindowGroup {
            RootView(store: store)
                .task {
                    guard live else { return }
                    store.start()
                    await store.connect()
                }
                // Back in front without a link: one connect (R_IOS_POWER_SAVE: a user action, no loop).
                .onChange(of: scenePhase) { old, phase in
                    guard live, phase == .active, old != .active else { return }
                    Task { await store.connectOnForeground() }
                }
        }
    }
}

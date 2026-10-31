import CaliBLE
import SwiftUI

/// The app: the real Bluetooth transport, or under `-UITestFixture <name>` the fake transport with
/// that fixture (UI tests never use real Bluetooth).
///
/// - Requirement: R_IOS_BLE_DIRECT, R_IOS_BACKGROUND_LINK, R_IOS_TESTED
@main
struct CamperApp: App {
    @State private var store: CamperStore
    private let live: Bool

    init() {
        let args = ProcessInfo.processInfo.arguments
        if let i = args.firstIndex(of: "-UITestFixture"), i + 1 < args.count,
           let fixture = Fixture(rawValue: args[i + 1]) {
            _store = State(initialValue: .preview(fixture))
            live = fixture.isLive
        } else {
            // The restore identifier lets iOS relaunch the app for the held link (camping on).
            let transport = CoreBluetoothTransport(restoreIdentifier: "de.opencalifornia.camper.central")
            // The last state, with its age, until this link has read the unit (never under a fixture).
            let url = CamperStore.defaultSaveURL
            _store = State(initialValue: CamperStore(
                session: CamperSession(transport: transport, clock: ContinuousClock()),
                state: CamperStore.restore(from: url) ?? CamperState(), saveURL: url))
            live = true
        }
    }

    var body: some Scene {
        WindowGroup {
            RootView(store: store)
                .task {
                    guard live else { return }
                    store.start()
                    await store.connect()
                }
        }
    }
}

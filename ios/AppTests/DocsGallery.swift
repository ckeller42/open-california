import SnapshotTesting
import SwiftUI
import Testing
import UIKit

@testable import Camper

/// Not a regression test: the generator for the docs screenshot gallery. It runs only with
/// `TEST_RUNNER_GALLERY=1` in the environment (a no-op otherwise, so CI's normal test run skips it),
/// and renders fixture-backed screens — the mock unit, no real data — into the app's Documents
/// container. A host step (`tools/ios_gallery.sh`) copies them to `docs/screenshots/ios/` for the
/// "Screens" section of `docs/ios-app.md`. The hosted test cannot write to the repo directly, so the
/// images leave the simulator through the app container.
@MainActor
struct DocsGallery {
    @Test func generate() async throws {
        guard ProcessInfo.processInfo.environment["GALLERY"] == "1" else { return }
        let dir = try FileManager.default.url(
            for: .documentDirectory, in: .userDomainMask, appropriateFor: nil, create: true
        ).appending(path: "gallery")
        try FileManager.default.createDirectory(at: dir, withIntermediateDirectories: true)

        let now = Date(timeIntervalSince1970: 1_800_000_000)
        let traits = UITraitCollection {
            $0.userInterfaceStyle = .light
            $0.displayScale = 2
            $0.preferredContentSizeCategory = .large
        }
        let device = SwiftUISnapshotLayout.device(config: .iPhone13)
        // Setup scrolls past one screen; a tall layout shows the whole page, including the alerts card.
        let tall = SwiftUISnapshotLayout.fixed(width: 390, height: 1150)
        let shots: [(String, Fixture, SwiftUISnapshotLayout, @MainActor (CamperStore) -> AnyView)] = [
            ("cockpit", .campingOn, device, { AnyView(CockpitView(store: $0)) }),
            ("energy", .campingOn, device, { AnyView(EnergyView(store: $0)) }),
            ("water", .campingOn, device, { AnyView(WaterView(store: $0)) }),
            ("lighting", .campingOn, device, { AnyView(LightsView(store: $0)) }),
            ("air-heater", .campingOn, device, { AnyView(AirHeaterView(store: $0)) }),
            ("cockpit-faults", .faults, device, { AnyView(CockpitView(store: $0)) }),
            ("setup", .parked, tall, { AnyView(SetupView(store: $0)) }),
        ]
        for (name, fixture, layout, make) in shots {
            let store = CamperStore.preview(fixture, now: now)
            let view = NavigationStack { make(store) }.environment(\.locale, Locale(identifier: "en_US"))
            let snapshotting = Snapshotting<AnyView, UIImage>.image(layout: layout, traits: traits)
            let image: UIImage = await withCheckedContinuation { cont in
                snapshotting.snapshot(AnyView(view)).run { cont.resume(returning: $0) }
            }
            try image.pngData()?.write(to: dir.appending(path: "\(name).png"))
        }
    }
}

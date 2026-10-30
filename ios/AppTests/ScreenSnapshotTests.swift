import SnapshotTesting
import SwiftUI
import Testing
import UIKit
@testable import Camper

/// Visual regression of the status screens: every fixture in light and dark. References live in
/// `__Snapshots__/ScreenSnapshotTests/`; record them with `TEST_RUNNER_SNAPSHOT_TESTING_RECORD=all`
/// on the pinned simulator (iPhone 17, iOS 26.2) and an English device language.
@MainActor
@Suite(.serialized)
struct ScreenSnapshotTests {
    /// A fixed clock: the "as of" ages in the references do not move.
    private static let now = Date(timeIntervalSince1970: 1_800_000_000)

    private func check(_ view: some View, _ name: String, file: StaticString = #filePath, line: UInt = #line) {
        for style in [UIUserInterfaceStyle.light, .dark] {
            // Scale 1 keeps the references small; the layout is the iPhone 13 portrait frame.
            let traits = UITraitCollection { $0.userInterfaceStyle = style; $0.displayScale = 1 }
            assertSnapshot(of: view, as: .image(perceptualPrecision: 0.98, layout: .device(config: .iPhone13), traits: traits),
                           named: "\(name)-\(style == .dark ? "dark" : "light")", fileID: #fileID, file: file, testName: "screens", line: line)
        }
    }

    /// - Test: T_IOS_SNAPSHOT_SCREENS
    /// - Verifies: R_IOS_LOOK, R_IOS_INSTALLED, R_IOS_TESTED
    @Test(arguments: Fixture.allCases)
    func rendersInDarkAndLight(_ fixture: Fixture) {
        let store = CamperStore.preview(fixture, now: Self.now)
        check(NavigationStack { CockpitView(store: store) }, "cockpit-\(fixture.rawValue)")
        check(NavigationStack { EnergyView(store: store) }, "energy-\(fixture.rawValue)")
        check(NavigationStack { WaterView(store: store) }, "water-\(fixture.rawValue)")
    }

    /// A held water reading shows its measurement age, never as live.
    ///
    /// - Test: T_IOS_SNAPSHOT_WATER_HELD
    /// - Verifies: R_IOS_HONEST
    @Test func heldWaterShowsItsAge() {
        let store = CamperStore.preview(.parked, now: Self.now)
        store.state.waterHeld = true
        store.state.waterGoodAt = Self.now.addingTimeInterval(-2 * 3600)
        #expect(String(localized: WaterView.measured(store)) == "measured 2 h ago")
        check(NavigationStack { WaterView(store: store) }, "water-held")
        check(NavigationStack { CockpitView(store: store) }, "cockpit-water-held")
    }
}

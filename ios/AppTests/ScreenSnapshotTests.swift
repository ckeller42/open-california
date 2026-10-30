import SnapshotTesting
import SwiftUI
import Testing
import UIKit
@testable import Camper

/// Visual regression of the status screens: every fixture in light and dark, plus German, the largest
/// accessibility text size and the small-phone layout. References live in
/// `__Snapshots__/ScreenSnapshotTests/`; record them with `TEST_RUNNER_SNAPSHOT_TESTING_RECORD=all`
/// on the pinned simulator (iPhone 17, iOS 26.2). The traits pin the text size, and the views take
/// their locale (number format, texts) from the environment, which each check sets; the scheme's
/// `en`/`US` covers what is resolved outside the view.
@MainActor
@Suite(.serialized)
struct ScreenSnapshotTests {
    /// A fixed clock: the "as of" ages in the references do not move.
    private static let now = Date(timeIntervalSince1970: 1_800_000_000)

    private static let english = Locale(identifier: "en_US")

    /// One snapshot. Scale 1 keeps the references small; the text size is pinned to the default.
    private func snap(_ view: some View, _ name: String, style: UIUserInterfaceStyle = .light,
                      locale: Locale = english, size: UIContentSizeCategory = .large,
                      layout: SwiftUISnapshotLayout = .device(config: .iPhone13),
                      file: StaticString = #filePath, line: UInt = #line) {
        let traits = UITraitCollection {
            $0.userInterfaceStyle = style
            $0.displayScale = 1
            $0.preferredContentSizeCategory = size
        }
        assertSnapshot(of: view.environment(\.locale, locale), as: .image(perceptualPrecision: 0.98, layout: layout, traits: traits),
                       named: name, fileID: #fileID, file: file, testName: "screens", line: line)
    }

    private func check(_ view: some View, _ name: String, file: StaticString = #filePath, line: UInt = #line) {
        snap(view, "\(name)-light", file: file, line: line)
        snap(view, "\(name)-dark", style: .dark, file: file, line: line)
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
        #expect(WaterView.measured(store, locale: Self.english) == "measured 2 h ago")
        #expect(WaterView.measured(store, locale: Locale(identifier: "de_DE")) == "vor 2 Std gemessen")
        check(NavigationStack { WaterView(store: store) }, "water-held")
        check(NavigationStack { CockpitView(store: store) }, "cockpit-water-held")
    }

    /// The full fixture (camping on, every function installed, a held water reading) in German, at
    /// the largest accessibility text size, and at the iPhone SE width (375 pt, the SE models that
    /// run iOS 17). The last two show the whole scroll height, not one screen. Light mode only. A
    /// clipped or overlapping element here is a layout bug.
    ///
    /// - Test: T_IOS_SNAPSHOT_VARIANTS
    /// - Verifies: R_IOS_LOOK, R_IOS_I18N
    @Test func rendersInGermanLargestTextAndSmallPhone() {
        let store = CamperStore.preview(.campingOn, now: Self.now)
        store.state.waterHeld = true
        store.state.waterGoodAt = Self.now.addingTimeInterval(-2 * 3600)
        let screens: [(String, AnyView)] = [
            ("cockpit", AnyView(NavigationStack { CockpitView(store: store) })),
            ("energy", AnyView(NavigationStack { EnergyView(store: store) })),
            ("water", AnyView(NavigationStack { WaterView(store: store) })),
        ]
        for (name, view) in screens {
            snap(view, "\(name)-de", locale: Locale(identifier: "de_DE"))
            snap(view, "\(name)-ax5", size: .accessibilityExtraExtraExtraLarge, layout: .fixed(width: 390, height: 2400))
            snap(view, "\(name)-se", layout: .fixed(width: 375, height: 1000))
        }
    }

    /// The page redraws by itself when the data turns stale, without a new frame: a state 88.5 s
    /// old (real clock) is "live"; 90 s after its frame the hosted pill shows "offline" + "as of …",
    /// and the content is drawn again (its controls disable then). Without the tick's clock in the
    /// pill the two renders are equal; without the content's stale tick the content is not redrawn.
    ///
    /// - Test: T_IOS_PILL_TICK
    /// - Verifies: R_IOS_HONEST
    @Test func pageRedrawsWhenTheDataTurnsStale() async throws {
        final class Count { var n = 0 }
        let count = Count()
        let store = CamperStore.preview(.parked)
        store.state.asOf = Date().addingTimeInterval(-88.5)
        // A window in the host app's scene: a TimelineView ticks only on screen.
        let scene = try #require(UIApplication.shared.connectedScenes.first as? UIWindowScene)
        let window = UIWindow(windowScene: scene)
        window.rootViewController = UIHostingController(rootView: StatusPage(store: store) {
            let _ = count.n += 1
            EmptyView()
        }.environment(\.locale, Self.english))
        window.makeKeyAndVisible()
        defer { window.isHidden = true }
        func render() -> Data {
            UIGraphicsImageRenderer(bounds: window.bounds).pngData { _ in
                _ = window.drawHierarchy(in: window.bounds, afterScreenUpdates: true)
            }
        }
        try await Task.sleep(for: .milliseconds(300))
        #expect(!store.isStale)
        let fresh = render(), drawn = count.n
        try await Task.sleep(for: .milliseconds(2500))  // past asOf + 90.5 s: the stale tick
        #expect(store.isStale)
        #expect(render() != fresh)
        #expect(count.n > drawn)
    }
}

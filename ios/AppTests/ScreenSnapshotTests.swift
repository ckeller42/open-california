import SnapshotTesting
import SwiftUI
import Testing
import UIKit
import CaliBLE
import CaliCore
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
        #expect(WaterView.measured(store, locale: Self.english) == "measured 2\u{00A0}h ago")
        #expect(WaterView.measured(store, locale: Locale(identifier: "de_DE")) == "vor 2\u{00A0}Std gemessen")
        check(NavigationStack { WaterView(store: store) }, "water-held")
        check(NavigationStack { CockpitView(store: store) }, "cockpit-water-held")
    }

    /// Two fixtures in German, at the largest accessibility text size and at the iPhone SE width
    /// (375 pt, the SE models that run iOS 17): the full one (camping on, every function installed, a
    /// held water reading) and `faults` (30 %, two energy faults, a water alert, a "—" value, the
    /// power-save rest). The last two variants show the whole scroll height, not one screen. Light
    /// mode only. A clipped or overlapping element here is a layout bug.
    ///
    /// - Test: T_IOS_SNAPSHOT_VARIANTS
    /// - Verifies: R_IOS_LOOK, R_IOS_I18N
    @Test(arguments: [Fixture.campingOn, .faults])
    func rendersInGermanLargestTextAndSmallPhone(_ fixture: Fixture) {
        let store = CamperStore.preview(fixture, now: Self.now)
        if fixture == .campingOn {
            store.state.waterHeld = true
            store.state.waterGoodAt = Self.now.addingTimeInterval(-2 * 3600)
        }
        let tag = fixture == .campingOn ? "" : "-\(fixture.rawValue)"
        let screens: [(String, AnyView)] = [
            ("cockpit", AnyView(NavigationStack { CockpitView(store: store) })),
            ("energy", AnyView(NavigationStack { EnergyView(store: store) })),
            ("water", AnyView(NavigationStack { WaterView(store: store) })),
        ]
        for (name, view) in screens {
            snap(view, "\(name)\(tag)-de", locale: Locale(identifier: "de_DE"))
            snap(view, "\(name)\(tag)-ax5", size: .accessibilityExtraExtraExtraLarge, layout: .fixed(width: 390, height: 3200))
            snap(view, "\(name)\(tag)-se", layout: .fixed(width: 375, height: 1000))
        }
    }

    /// The app's frame: tab bar with its labels, navigation title and the page under it, in English
    /// and German.
    ///
    /// - Test: T_IOS_SNAPSHOT_ROOT
    /// - Verifies: R_IOS_LOOK, R_IOS_I18N
    @Test func rendersTheTabs() {
        for (name, locale) in [("root", Self.english), ("root-de", Locale(identifier: "de_DE"))] {
            // As a view controller in a window: the floating tab bar and the navigation bar draw only there.
            let host = UIHostingController(rootView: RootView(store: CamperStore.preview(.parked, now: Self.now))
                .environment(\.locale, locale))
            let traits = UITraitCollection { $0.userInterfaceStyle = .light; $0.displayScale = 1; $0.preferredContentSizeCategory = .large }
            assertSnapshot(of: host, as: .image(drawHierarchyInKeyWindow: true, perceptualPrecision: 0.98, size: CGSize(width: 390, height: 844), traits: traits), named: name, testName: "screens")
        }
    }

    /// Every control screen in light and dark for a parked unit, camping on and an unsupported (V3)
    /// unit (every control locked, with the reason); camping on also in German, at the largest
    /// accessibility text size (whole scroll height) and at 375 pt.
    ///
    /// - Test: T_IOS_SNAPSHOT_CONTROLS
    /// - Verifies: R_IOS_LOOK, R_IOS_FUNCTIONS, R_IOS_V2_ONLY, R_IOS_I18N
    @Test(arguments: [Fixture.parked, .campingOn, .unsupportedV3])
    func controlScreensSnapshot(_ fixture: Fixture) {
        let store = CamperStore.preview(fixture, now: Self.now)
        for screen in [ControlScreen.camping, .lighting, .cooler, .airHeater] {
            let view = NavigationStack { screen.view(store) }
            let name = "control-\(screen)-\(fixture.rawValue)"
            check(view, name)
            if fixture == .campingOn {
                snap(view, "\(name)-de", locale: Locale(identifier: "de_DE"))
                snap(view, "\(name)-ax5", size: .accessibilityExtraExtraExtraLarge, layout: .fixed(width: 390, height: 3600))
                snap(view, "\(name)-se", layout: .fixed(width: 375, height: 1400))
            }
        }
    }

    /// A control screen locked in the rest (the tap-to-connect card), while connecting and offline;
    /// the cooler and Lighting while a command is on its way, Lighting with a result; the cockpit in
    /// the cooldown after a tap; the air
    /// heater with an error, running and in continuous heating.
    ///
    /// - Test: T_IOS_SNAPSHOT_CONTROL_STATES
    /// - Verifies: R_IOS_LOOK, R_IOS_HONEST, R_IOS_POWER_SAVE
    @Test func controlScreenStates() {
        let states: [(String, LinkState)] = [("rest", .rest(until: Self.now.addingTimeInterval(60))), ("connecting", .connecting), ("offline", .off)]
        for (name, link) in states {
            let store = CamperStore.preview(.parked, now: Self.now)
            store.link = link
            if name == "offline" { store.state.asOf = Self.now.addingTimeInterval(-600) }
            check(NavigationStack { CampingView(store: store) }, "control-camping-\(name)")
        }
        let sending = CamperStore.preview(.campingOn, now: Self.now)
        sending.feedback = .sending(function: "cooler")
        check(NavigationStack { CoolerView(store: sending) }, "control-cooler-sending")

        // Lighting, the long page: the feedback stays in sight over the content.
        for (name, feedback) in [("sending", Feedback.sending(function: "lighting")), ("result", .result(.sent, function: "lighting"))] {
            let store = CamperStore.preview(.campingOn, now: Self.now)
            store.feedback = feedback
            snap(NavigationStack { LightsView(store: store) }, "control-lighting-\(name)-light")
            snap(NavigationStack { LightsView(store: store) }, "control-lighting-\(name)-de", locale: Locale(identifier: "de_DE"))
        }

        let cooldown = CamperStore.preview(.faults, now: Self.now)
        cooldown.feedback = .connecting(until: Self.now.addingTimeInterval(5))
        check(NavigationStack { CockpitView(store: cooldown) }, "cockpit-rest-cooldown")

        let heater: [(String, [String: JSONValue])] = [
            ("error", ["error": .string("low_fuel"), "error_code": .number(2)]),
            ("running", ["running": .bool(true), "normal": .bool(true), "level": .number(5), "running_time": .number(60),
                         "running_time_remaining": .number(45)]),
            ("permanent", ["running": .bool(true), "permanent": .bool(true), "level": .number(10), "running_time": .number(60)]),
        ]
        for (name, values) in heater {
            let store = CamperStore.preview(.campingOn, now: Self.now)
            store.state.interpreted["airheater", default: [:]].merge(values) { _, new in new }
            check(NavigationStack { AirHeaterView(store: store) }, "control-airHeater-\(name)")
        }
    }

    /// Every feedback the user can see: each command result (each refusal and failure), sending,
    /// connecting and the cooldown, in English and German.
    ///
    /// - Test: T_IOS_SNAPSHOT_BANNERS
    /// - Verifies: R_IOS_HONEST, R_IOS_I18N
    @Test func feedbackBanners() {
        let all: [Feedback] = [.sending(function: "cooler"), .connecting(until: nil), .connecting(until: Self.now.addingTimeInterval(5)),
                               .connect(.resting(until: Self.now.addingTimeInterval(5))),
                               .connect(.failed(.unreachable)), .result(.sent, function: "lighting")]
            + CommandMappingTests.allResults.map { .result($0, function: "cooler") }
        let gallery = VStack(spacing: 8) {
            ForEach(all.indices, id: \.self) { FeedbackBanner(feedback: all[$0], clock: { Self.now }) }
        }.padding()
        snap(gallery, "banners", layout: .fixed(width: 390, height: 2800))
        snap(gallery, "banners-de", locale: Locale(identifier: "de_DE"), layout: .fixed(width: 390, height: 3000))
        snap(gallery, "banners-dark", style: .dark, layout: .fixed(width: 390, height: 2800))
    }

    /// The page redraws by itself when the data turns stale, without a new frame: with the store's
    /// clock 88 s after the frame the page is "live"; the clock moves to 120 s and, on the next 1 s
    /// tick, the hosted pill shows "offline" + "as of …" and the content is drawn again (its controls
    /// disable then). Without the tick's clock in the pill the two renders are equal; without the
    /// page's change state the content is not redrawn. The clock is injected; only the tick is real.
    ///
    /// - Test: T_IOS_PILL_TICK
    /// - Verifies: R_IOS_HONEST
    @Test func pageRedrawsWhenTheDataTurnsStale() async throws {
        final class Count { var n = 0 }
        let count = Count()
        var clock = Self.now.addingTimeInterval(88)
        let store = CamperStore.preview(.parked, now: Self.now)
        store.now = { clock }
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
        clock = Self.now.addingTimeInterval(120)
        try await Task.sleep(for: .milliseconds(1500))  // at least one 1 s tick
        #expect(store.isStale)
        #expect(render() != fresh)
        #expect(count.n > drawn)
    }
}

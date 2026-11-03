import XCTest

/// The bench lab's screen tour: every tab and control screen of the app on a real iPhone that is
/// live with the fake camper unit, as screenshots for a UI and UX review. `LAB_TOUR` names the
/// variant: `light`, `dark`, `de` (German) or `ax5` (the largest accessibility text size). Each
/// screen is shot from the top and after each swipe until the page stops moving. Without `LAB_TOUR`
/// the test skips: CI and the simulator never run it.
final class LabTourUITests: XCTestCase {
    private static let tiles = ["Camping mode", "Lighting", "Cooler", "Air heater"]
    private static let tilesDE = ["Campingmodus", "Beleuchtung", "Kühlbox", "Luftstandheizung"]
    private static let tabs = ["Energy", "Water", "Setup"]
    private static let tabsDE = ["Energie", "Wasser", "Einrichtung"]

    @MainActor
    private func shot(_ app: XCUIApplication, _ name: String) {
        let a = XCTAttachment(screenshot: app.screenshot())
        a.name = name
        a.lifetime = .keepAlways
        add(a)
    }

    /// Shots of the whole page: the top, then one per swipe until the screen stops changing.
    @MainActor
    private func page(_ app: XCUIApplication, _ name: String) {
        Thread.sleep(forTimeInterval: 1.5)
        shot(app, "\(name)-0")
        var last = app.screenshot().pngRepresentation
        for i in 1...5 {
            app.swipeUp()
            Thread.sleep(forTimeInterval: 1)
            let now = app.screenshot().pngRepresentation
            if now == last { break }
            shot(app, "\(name)-\(i)")
            last = now
        }
        for _ in 0..<5 { app.swipeDown() }
    }

    @MainActor
    func testTour() throws {
        guard let variant = ProcessInfo.processInfo.environment["LAB_TOUR"], !variant.isEmpty else {
            throw XCTSkip("lab only: set TEST_RUNNER_LAB_TOUR")
        }
        let de = variant == "de"
        let app = XCUIApplication()
        app.launchArguments =
            de ? ["-AppleLanguages", "(de)", "-AppleLocale", "de_DE"] : ["-AppleLanguages", "(en)", "-AppleLocale", "en_US"]
        if variant == "dark" { app.launchArguments += ["-UIUserInterfaceStyle", "dark"] }
        if variant == "light" { app.launchArguments += ["-UIUserInterfaceStyle", "light"] }
        if variant == "ax5" {
            app.launchArguments += ["-UIPreferredContentSizeCategoryName", "UICTContentSizeCategoryAccessibilityXXXL"]
        }
        app.launch()
        XCTAssertTrue(app.staticTexts["live"].waitForExistence(timeout: 30), "a live link")
        page(app, "\(variant)-cockpit")

        for tile in de ? Self.tilesDE : Self.tiles {
            let button = app.buttons[tile]
            if !button.waitForExistence(timeout: 5) {
                shot(app, "\(variant)-missing-\(tile)")
                continue
            }
            if !button.isHittable { app.swipeUp() }
            button.tap()
            page(app, "\(variant)-\(tile)")
            app.navigationBars.buttons.element(boundBy: 0).tap()
        }
        for tab in de ? Self.tabsDE : Self.tabs {
            let button = app.tabBars.buttons[tab]
            if !button.waitForExistence(timeout: 5) {
                shot(app, "\(variant)-missing-\(tab)")
                continue
            }
            button.tap()
            page(app, "\(variant)-\(tab)")
        }
        app.tabBars.buttons.element(boundBy: 0).tap()
    }
}

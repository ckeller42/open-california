import XCTest

/// The cockpit, energy and water screens over fixtures (`-UITestFixture <name>`), each with an
/// accessibility audit.
final class CockpitUITests: XCTestCase {
    @MainActor
    private func launch(_ fixture: String, dark: Bool = false) -> XCUIApplication {
        let app = XCUIApplication()
        app.launchArguments = ["-UITestFixture", fixture, "-AppleLanguages", "(en)", "-AppleLocale", "en_US"]
        if dark { app.launchArguments += ["-UIUserInterfaceStyle", "dark"] }
        app.launch()
        return app
    }

    /// The battery ring and the fresh-water value show (the `unreachable` fixture: a live read pass
    /// stores no water on a cold start). This van reports no solar and the unit reports no outside
    /// temperature, so neither small value exists.
    ///
    /// - Test: T_IOS_UI_COCKPIT_VALUES
    /// - Verifies: R_IOS_LOOK, R_IOS_INSTALLED
    @MainActor
    func testCockpitShowsBatteryAndWaterButNoSolarOrOutsideTemperature() throws {
        let app = launch("unreachable")
        XCTAssertTrue(app.otherElements["battery-ring"].waitForExistence(timeout: 10))
        XCTAssertTrue(app.descendants(matching: .any).matching(NSPredicate(format: "label BEGINSWITH 'Fresh water'")).firstMatch.exists)
        XCTAssertFalse(app.staticTexts["Solar power"].exists)
        XCTAssertFalse(app.descendants(matching: .any).matching(NSPredicate(format: "label CONTAINS '°C'")).firstMatch.exists)
        for name in ["Camping mode", "Lighting", "Cooler", "Air heater"] {
            XCTAssertTrue(app.buttons[name].exists, name)
        }
        try app.audit()
    }

    /// - Test: T_IOS_UI_NOT_INSTALLED
    /// - Verifies: R_IOS_INSTALLED
    @MainActor
    func testNotInstalledFunctionHasNoTile() throws {
        let app = launch("noCooler")
        XCTAssertTrue(app.buttons["Camping mode"].waitForExistence(timeout: 10))
        XCTAssertFalse(app.buttons["Cooler"].exists)
        try app.audit()
    }

    /// Review Focus 4: a state hours old shows its age, and the controls are disabled.
    ///
    /// - Test: T_IOS_UI_STALE_AS_OF
    /// - Verifies: R_IOS_HONEST, R_IOS_BACKGROUND_LINK
    @MainActor
    func testStaleRestoreShowsAsOf() throws {
        let app = launch("staleRestore")
        // The pill is one element for VoiceOver, with the age in full words.
        XCTAssertTrue(app.staticTexts["offline, as of 5 hours ago"].waitForExistence(timeout: 10))
        XCTAssertFalse(app.buttons["Camping mode"].isEnabled)
        try app.audit()
    }

    /// Every tab passes the accessibility audit, in light and dark.
    ///
    /// - Test: T_IOS_UI_COCKPIT_AUDIT
    /// - Verifies: R_IOS_LOOK, R_IOS_TESTED
    @MainActor
    func testCockpitPassesAccessibilityAudit() throws {
        for dark in [false, true] {
            let app = launch("campingOn", dark: dark)
            XCTAssertTrue(app.otherElements["battery-ring"].waitForExistence(timeout: 10))
            try app.audit()
            app.swipeUp()  // the tiles near the tab bar, scrolled into view
            try app.audit()
            for tab in ["Energy", "Water"] {
                app.tabBars.buttons[tab].tap()
                XCTAssertTrue(app.navigationBars[tab].waitForExistence(timeout: 5))
                try app.audit()
            }
            app.terminate()
        }
    }
}

/// The control screens, opened from the cockpit buttons, each with the accessibility audit.
final class ControlUITests: XCTestCase {
    /// Each cockpit button opens its control screen (none is a no-op); every control screen and the
    /// energy mode pass the audit, in light and dark, also scrolled to the end.
    ///
    /// - Test: T_IOS_UI_CONTROLS_AUDIT
    /// - Verifies: R_IOS_LOOK, R_IOS_FUNCTIONS, R_IOS_TESTED
    @MainActor
    func testControlScreensPassAccessibilityAudit() throws {
        for dark in [false, true] {
            let app = XCUIApplication()
            app.launchArguments = ["-UITestFixture", "campingOn", "-AppleLanguages", "(en)", "-AppleLocale", "en_US"]
            if dark { app.launchArguments += ["-UIUserInterfaceStyle", "dark"] }
            app.launch()
            for (button, title, control) in [("Camping mode", "Camping mode", "Rear USB ports"), ("Lighting", "Lighting", "All lights"),
                                             ("Cooler", "Cooler", "Refrigerator box"), ("Air heater", "Air heater", "Immediate heating")] {
                let tile = app.buttons[button]
                XCTAssertTrue(tile.waitForExistence(timeout: 10), button)
                if !tile.isHittable { app.swipeUp() }
                tile.tap()
                XCTAssertTrue(app.navigationBars[title].waitForExistence(timeout: 5), title)
                XCTAssertTrue(app.descendants(matching: .any)[control].exists, control)
                Thread.sleep(forTimeInterval: 1)  // the push animation ends
                print("AUDIT screen \(title)")
                try app.audit()
                // A drag with a hold: the content rests where the finger stops (no momentum, no bounce).
                let scroll = app.scrollViews.firstMatch
                scroll.coordinate(withNormalizedOffset: CGVector(dx: 0.5, dy: 0.85)).press(
                    forDuration: 0.2, thenDragTo: scroll.coordinate(withNormalizedOffset: CGVector(dx: 0.5, dy: 0.15)),
                    withVelocity: .default, thenHoldForDuration: 0.5)
                Thread.sleep(forTimeInterval: 1)
                print("AUDIT screen \(title), scrolled")
                try app.audit()
                app.navigationBars[title].buttons.element(boundBy: 0).tap()
                XCTAssertTrue(app.navigationBars["Cockpit"].waitForExistence(timeout: 5), app.debugDescription)
            }
            app.tabBars.buttons["Energy"].tap()
            XCTAssertTrue(app.descendants(matching: .any)["Energy mode"].waitForExistence(timeout: 5))
            try app.audit()
            app.terminate()
        }
    }
}

extension XCUIApplication {
    /// The accessibility audit; each issue is logged with its element.
    ///
    /// Exemption (the only one): a contrast, Dynamic Type or clipping issue of an element that is
    /// not fully inside the clear part of the screen — under the navigation bar or the floating tab
    /// bar, in the 44 pt band next to either where the system blurs scrolled content (the scroll edge
    /// effect), or scrolled off the screen. There the element is measured against
    /// the blur or against nothing, not its own background; the tests audit each screen again
    /// after a scroll, where the same element is audited like any other. The tab bar band also
    /// covers the system tab bar items themselves. Each exemption is recorded as an activity with an
    /// attachment, so it shows in the `.xcresult`.
    @MainActor
    func audit() throws {
        let screen = windows.firstMatch.frame
        let top = navigationBars.firstMatch.exists ? navigationBars.firstMatch.frame.maxY + 44 : screen.minY
        let bottom = tabBars.firstMatch.exists ? tabBars.firstMatch.frame.minY - 44 : screen.maxY
        let clear = CGRect(x: screen.minX, y: top, width: screen.width, height: max(0, bottom - top))
        try performAccessibilityAudit { issue in
            let outside = issue.element.map { !clear.contains($0.frame) } ?? false
                && [.contrast, .dynamicType, .textClipped].contains(issue.auditType)
            print("AUDIT \(outside ? "exempt (outside the clear screen) " : "")\(issue.compactDescription) | \(issue.element?.label ?? "-") \(issue.element.map { "\($0.frame)" } ?? "") | \(issue.detailedDescription)")
            if outside {
                XCTContext.runActivity(named: "Audit exemption (outside the clear screen): \(issue.compactDescription)") { activity in
                    activity.add(XCTAttachment(string: "\(issue.element?.label ?? "-")\n\(issue.detailedDescription)"))
                }
            }
            return outside
        }
    }
}

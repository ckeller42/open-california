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
        for name in ["Camping mode", "Lights", "Cooler", "Air heater"] {
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
        XCTAssertTrue(app.staticTexts["as of 5 h ago"].waitForExistence(timeout: 10))
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

extension XCUIApplication {
    /// The accessibility audit; each issue is logged with its element.
    ///
    /// Exemption (the only one): a contrast, Dynamic Type or clipping issue of an element under the
    /// floating tab bar or in the band above it where the system blurs scrolled content (the scroll
    /// edge effect, taken as 44 pt). There the element is measured against the blur, not its own
    /// background; scrolled into view, the same element is audited like any other. The band also
    /// covers the system tab bar items themselves. Each exemption is recorded as an activity with an
    /// attachment, so it shows in the `.xcresult`.
    @MainActor
    func audit() throws {
        let tab = tabBars.firstMatch.exists ? tabBars.firstMatch.frame : .null
        let bar = tab.isNull ? tab : CGRect(x: tab.minX, y: tab.minY - 44, width: tab.width, height: tab.height + 44)
        try performAccessibilityAudit { issue in
            let underBar = issue.element.map { $0.frame.intersects(bar) } ?? false
                && [.contrast, .dynamicType, .textClipped].contains(issue.auditType)
            print("AUDIT \(underBar ? "exempt (under the tab bar) " : "")\(issue.compactDescription) | \(issue.element?.label ?? "-") | \(issue.detailedDescription)")
            if underBar {
                XCTContext.runActivity(named: "Audit exemption (under the tab bar): \(issue.compactDescription)") { activity in
                    activity.add(XCTAttachment(string: "\(issue.element?.label ?? "-")\n\(issue.detailedDescription)"))
                }
            }
            return underBar
        }
    }
}

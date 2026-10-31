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
        try app.auditScreen()
    }

    /// - Test: T_IOS_UI_NOT_INSTALLED
    /// - Verifies: R_IOS_INSTALLED
    @MainActor
    func testNotInstalledFunctionHasNoTile() throws {
        let app = launch("noCooler")
        XCTAssertTrue(app.buttons["Camping mode"].waitForExistence(timeout: 10))
        XCTAssertFalse(app.buttons["Cooler"].exists)
        try app.auditScreen()
    }

    /// Review Focus 4: a state hours old shows its age. The button still opens its screen, as the
    /// web UI's tiles do, and there the controls are locked with the reason.
    ///
    /// - Test: T_IOS_UI_STALE_AS_OF
    /// - Verifies: R_IOS_HONEST, R_IOS_BACKGROUND_LINK
    @MainActor
    func testStaleRestoreShowsAsOf() throws {
        let app = launch("staleRestore")
        // The pill is one element for VoiceOver, with the age in full words.
        XCTAssertTrue(app.staticTexts["offline, as of 5 hours ago"].waitForExistence(timeout: 10))
        try app.auditScreen()
        app.buttons["Camping mode"].tap()
        XCTAssertTrue(app.navigationBars["Camping mode"].waitForExistence(timeout: 5))
        XCTAssertTrue(app.staticTexts["Not available while the van is offline"].exists)
        try app.auditScreen()
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
            try app.auditScreen()
            for tab in ["Energy", "Water"] {
                app.tabBars.buttons[tab].tap()
                XCTAssertTrue(app.navigationBars[tab].waitForExistence(timeout: 5))
                try app.auditScreen()
            }
            app.terminate()
        }
    }
}

/// The control screens, opened from the cockpit buttons, each with the accessibility audit.
final class ControlUITests: XCTestCase {
    /// Each cockpit button opens its control screen (none is a no-op); every control screen and the
    /// energy mode pass the audit, in light and dark, over their whole scroll height.
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
            for (title, control) in [("Camping mode", "Rear USB ports"), ("Lighting", "All lights"),
                                     ("Cooler", "Refrigerator box"), ("Air heater", "Immediate heating")] {
                let tile = app.buttons[title]
                XCTAssertTrue(tile.waitForExistence(timeout: 10), title)
                if !tile.isHittable { app.swipeUp() }
                tile.tap()
                XCTAssertTrue(app.navigationBars[title].waitForExistence(timeout: 5), title)
                XCTAssertTrue(app.descendants(matching: .any)[control].exists, control)
                Thread.sleep(forTimeInterval: 1)  // the push animation ends
                try app.auditScreen()
                app.navigationBars[title].buttons.element(boundBy: 0).tap()
                XCTAssertTrue(app.navigationBars["Cockpit"].waitForExistence(timeout: 5))
            }
            app.tabBars.buttons["Energy"].tap()
            XCTAssertTrue(app.descendants(matching: .any)["Energy mode"].waitForExistence(timeout: 5))
            try app.auditScreen()
            app.terminate()
        }
    }
}

extension XCUIApplication {
    /// One audit pass: the labels it exempted, and the labels that were inside the clear part of the
    /// screen and had no issue.
    struct AuditPass {
        var exempt: [String] = []
        var clean: Set<String> = []
        /// Element-less text issues of a scrolled pass (glyphs cut at the edge under a bar).
        var cutGlyphs = 0
    }

    /// Audits the open screen over its whole height: at the top, then after each scroll until the
    /// content stops moving. An element exempt in one pass (see `audit(scrolled:)`) must be audited
    /// cleanly inside the clear part of the screen in another pass; on a screen that does not scroll
    /// nothing is exempt.
    @MainActor
    func auditScreen(file: StaticString = #filePath, line: UInt = #line) throws {
        Thread.sleep(forTimeInterval: 1)  // a launch read or a push has settled
        _ = stableFrames()
        var passes = [try audit(scrolled: false)]
        for _ in 0..<4 {
            let before = stableFrames()
            // A drag with a hold: the content rests where the finger stops (no momentum, no bounce).
            let area = windows.firstMatch
            // In the left margin, outside every card: a press opens or highlights nothing.
            area.coordinate(withNormalizedOffset: CGVector(dx: 0.02, dy: 0.75)).press(
                forDuration: 0.2, thenDragTo: area.coordinate(withNormalizedOffset: CGVector(dx: 0.02, dy: 0.35)),
                withVelocity: .default, thenHoldForDuration: 0.5)
            // Wait until the content rests (a page that fits bounces back), then see whether it moved.
            if stableFrames() == before { break }
            passes.append(try audit(scrolled: true))
        }
        let clean = passes.reduce(into: Set<String>()) { $0.formUnion($1.clean) }
        // A cut row leaves at most a few pieces; more would hide real text without an element.
        for p in passes where p.cutGlyphs > 12 { XCTFail("\(p.cutGlyphs) element-less text issues in one scrolled pass", file: file, line: line) }
        for label in passes.flatMap(\.exempt) where !clean.contains(label) {
            XCTFail("\(label): exempt, but never audited inside the clear screen", file: file, line: line)
        }
        // Back to the top for the caller.
        for _ in 0..<passes.count { swipeDown() }
    }

    /// `frames()` once two reads 0.3 s apart agree (at most 3 s).
    @MainActor
    private func stableFrames() -> [String: CGRect] {
        var last = frames()
        for _ in 0..<10 {
            Thread.sleep(forTimeInterval: 0.3)
            let now = frames()
            if now == last { return now }
            last = now
        }
        return last
    }

    /// The frames of the labelled elements (one snapshot: a cheap check whether the content moved).
    @MainActor
    private func frames() -> [String: CGRect] {
        var out: [String: CGRect] = [:]
        func walk(_ e: XCUIElementSnapshot) {
            if !e.label.isEmpty { out[e.label] = e.frame }
            e.children.forEach(walk)
        }
        if let s = try? snapshot() { walk(s) }
        return out
    }

    /// The accessibility audit; each issue is logged with its element.
    ///
    /// Exemption (the only one): a contrast, Dynamic Type or clipping issue of an element that is
    /// not fully inside the clear part of the screen — under the floating tab bar or in the 44 pt
    /// band above it, below the screen, and (only after a scroll, `scrolled`) under the navigation
    /// bar or in the 44 pt band under it. There the system blurs scrolled content (the scroll edge
    /// effect) or there is nothing to measure. `auditScreen` checks that each exempt element was
    /// audited cleanly in another pass. Also exempt after a scroll only: an "inaccessible text"
    /// issue with no element, from glyphs cut at the edge under a bar (at most 12 per pass). Each
    /// exemption is attached to the result (kept on success).
    @MainActor
    func audit(scrolled: Bool) throws -> AuditPass {
        print("AUDIT pass on \(navigationBars.firstMatch.identifier) scrolled=\(scrolled)")
        let screen = windows.firstMatch.frame
        let top = scrolled && navigationBars.firstMatch.exists ? navigationBars.firstMatch.frame.maxY + 44 : screen.minY
        let bottom = tabBars.firstMatch.exists ? tabBars.firstMatch.frame.minY - 44 : screen.maxY
        let clear = CGRect(x: screen.minX, y: top, width: screen.width, height: max(0, bottom - top))
        var pass = AuditPass()
        pass.clean = Set(frames().filter { clear.contains($0.value) }.keys)
        try performAccessibilityAudit { issue in
            let label = issue.element?.label ?? "-"
            pass.clean.remove(label)
            // After a scroll, the rows cut at the hard edge under the bars leave glyph pieces with no
            // element ("potentially inaccessible text"); the same rows are elements in the top pass.
            let cutGlyphs = scrolled && issue.element == nil && issue.auditType == .elementDetection
            if cutGlyphs { pass.cutGlyphs += 1 }
            let outside = cutGlyphs || issue.element.map { !clear.contains($0.frame) } ?? false
                && [.contrast, .dynamicType, .textClipped].contains(issue.auditType)
            print("AUDIT \(outside ? "exempt (outside the clear screen) " : "")\(issue.compactDescription) | \(label) \(issue.element.map { "\($0.frame)" } ?? "") | \(issue.detailedDescription)")
            if outside {
                if !cutGlyphs { pass.exempt.append(label) }
                XCTContext.runActivity(named: "Audit exemption (outside the clear screen): \(issue.compactDescription)") { activity in
                    let attachment = XCTAttachment(string: "\(label)\n\(issue.detailedDescription)")
                    attachment.lifetime = .keepAlways
                    activity.add(attachment)
                }
            }
            return outside
        }
        return pass
    }
}

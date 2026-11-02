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

    /// The battery ring and the fresh-water value show (the `unreachable` fixture: it does not
    /// connect at launch, so it keeps its state; a live read pass stores no water on a cold start).
    /// This van reports no solar and the unit reports no outside temperature, so neither small value
    /// exists.
    ///
    /// - Test: T_IOS_UI_COCKPIT_VALUES
    /// - Verifies: R_IOS_LOOK, R_IOS_INSTALLED
    @MainActor
    func testCockpitShowsBatteryAndWaterButNoSolarOrOutsideTemperature() throws {
        let app = launch("unreachable")
        XCTAssertTrue(app.otherElements["battery-ring"].waitForExistence(timeout: 10))
        XCTAssertTrue(
            app.descendants(matching: .any).matching(NSPredicate(format: "label BEGINSWITH 'Fresh water'")).firstMatch.exists)
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

    /// Review Focus 4: a state hours old shows its age, and the card under the pill offers a connect.
    ///
    /// - Test: T_IOS_UI_STALE_AS_OF
    /// - Verifies: R_IOS_HONEST, R_IOS_BACKGROUND_LINK
    @MainActor
    func testStaleRestoreShowsAsOf() throws {
        let app = launch("staleRestore")
        // The pill is one element for VoiceOver, with the age in full words.
        XCTAssertTrue(app.staticTexts["offline, as of 5 hours ago"].waitForExistence(timeout: 10))
        XCTAssertTrue(app.buttons["Tap to connect"].exists)
        try app.auditScreen()
    }

    /// Out of range at launch (the `unreachable` fixture does not connect): the "Tap to connect" card
    /// is the way back; once the unit is in range a tap connects, without a kill of the app.
    ///
    /// - Test: T_IOS_UI_TAP_TO_CONNECT
    /// - Verifies: R_IOS_POWER_SAVE, R_IOS_HONEST
    @MainActor
    func testUnreachableLaunchConnectsOnTap() throws {
        let app = launch("unreachable")
        let card = app.buttons["Tap to connect"]
        XCTAssertTrue(card.waitForExistence(timeout: 10))
        card.tap()
        XCTAssertTrue(app.staticTexts["live"].waitForExistence(timeout: 15))
        XCTAssertFalse(app.buttons["Tap to connect"].exists)
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
    /// energy mode pass the audit, in light, over their whole scroll height.
    ///
    /// - Test: T_IOS_UI_CONTROLS_AUDIT
    /// - Verifies: R_IOS_LOOK, R_IOS_FUNCTIONS, R_IOS_TESTED
    @MainActor
    func testControlScreensPassAccessibilityAudit() throws {
        try auditControlScreens(dark: false)
    }

    /// The same in dark; a test of its own, so each stays under the per-test time limit on CI.
    ///
    /// - Test: T_IOS_UI_CONTROLS_AUDIT_DARK
    /// - Verifies: R_IOS_LOOK, R_IOS_TESTED
    @MainActor
    func testControlScreensPassAccessibilityAuditInDark() throws {
        try auditControlScreens(dark: true)
    }

    @MainActor
    private func auditControlScreens(dark: Bool) throws {
        let app = XCUIApplication()
        app.launchArguments = ["-UITestFixture", "campingOn", "-AppleLanguages", "(en)", "-AppleLocale", "en_US"]
        if dark { app.launchArguments += ["-UIUserInterfaceStyle", "dark"] }
        app.launch()
        for (title, control) in [
            ("Camping mode", "Rear USB ports"), ("Lighting", "All lights"),
            ("Cooler", "Refrigerator box"), ("Air heater", "Immediate heating"),
        ] {
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

/// The Setup tab: the unit's status and the pairing guide, over the fake transport's one unit.
final class SetupUITests: XCTestCase {
    /// The paired guide is collapsed; "Pair again…" opens step 1, and the guide runs to the outcome
    /// "already paired" (the fake unit takes the pick at once, like an iPhone that shares the vendor
    /// app's bond); "Close" collapses it again. Each step passes the audit, in light and dark.
    ///
    /// - Test: T_IOS_UI_SETUP_AUDIT
    /// - Verifies: R_IOS_PAIRING, R_IOS_LOOK, R_IOS_TESTED
    @MainActor
    func testSetupPassesAccessibilityAudit() throws {
        for dark in [false, true] {
            let app = XCUIApplication()
            app.launchArguments = ["-UITestFixture", "parked", "-AppleLanguages", "(en)", "-AppleLocale", "en_US"]
            if dark { app.launchArguments += ["-UIUserInterfaceStyle", "dark"] }
            app.launch()
            app.tabBars.buttons["Setup"].tap()
            XCTAssertTrue(app.navigationBars["Setup"].waitForExistence(timeout: 5))
            // The fixture's launch connect pairs: the guide is collapsed.
            XCTAssertTrue(app.buttons["Pair again…"].waitForExistence(timeout: 10))
            try app.auditScreen()
            app.buttons["Pair again…"].tap()
            XCTAssertTrue(app.staticTexts["Pair the camper unit"].waitForExistence(timeout: 5))
            try app.auditScreen()
            app.buttons["I'm on that screen"].tap()
            let unit = app.buttons.matching(NSPredicate(format: "label BEGINSWITH '1. VWCAMPER'")).firstMatch
            XCTAssertTrue(unit.waitForExistence(timeout: 10))
            try app.auditScreen()
            unit.tap()
            XCTAssertTrue(app.staticTexts["This iPhone is already paired with the camper unit"].waitForExistence(timeout: 15))
            try app.auditScreen()
            app.buttons["Close"].tap()
            XCTAssertTrue(app.buttons["Pair again…"].waitForExistence(timeout: 5))
            app.terminate()
        }
    }

    /// "Forget this camper unit…" asks first; Cancel changes nothing; Forget shows the three forget
    /// steps and "not paired". Not run here (it needs an iOS 27 simulator).
    ///
    /// - Test: T_IOS_UI_FORGET_UNIT
    /// - Verifies: R_IOS_PAIRING, R_IOS_HONEST
    @MainActor
    func testForgetAsksAndShowsTheBluetoothSteps() throws {
        let app = XCUIApplication()
        app.launchArguments = ["-UITestFixture", "parked", "-AppleLanguages", "(en)", "-AppleLocale", "en_US"]
        app.launch()
        app.tabBars.buttons["Setup"].tap()
        let forget = app.buttons["Forget this camper unit…"]
        XCTAssertTrue(forget.waitForExistence(timeout: 10))

        forget.tap()
        let alert = app.alerts["Forget this camper unit?"]
        XCTAssertTrue(alert.waitForExistence(timeout: 5))
        XCTAssertTrue(alert.staticTexts.element(matching: NSPredicate(format: "label CONTAINS 'use Settings'")).exists)
        alert.buttons["Cancel"].tap()
        XCTAssertTrue(forget.waitForExistence(timeout: 5))
        XCTAssertTrue(app.staticTexts["This iPhone is paired with the camper unit."].exists)

        forget.tap()
        XCTAssertTrue(alert.waitForExistence(timeout: 5))
        alert.buttons["Forget"].tap()
        XCTAssertTrue(app.staticTexts["The app has forgotten the camper unit"].waitForExistence(timeout: 10))
        XCTAssertTrue(app.staticTexts["1. Open Settings → Bluetooth on this iPhone."].exists)
        XCTAssertTrue(app.staticTexts["3. Come back here and pair again."].exists)
        XCTAssertTrue(app.staticTexts["not paired"].waitForExistence(timeout: 5))
        app.terminate()
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
        /// The issues that are not exempt: `auditScreen` fails on each.
        var failures: [String] = []
        /// The labelled elements moved during the pass: it measured moving content.
        var moved = false
    }

    /// Audits the open screen over its whole height: at the top, then after each scroll until the
    /// content stops moving. An element exempt in one pass (see `audit(scrolled:)`) must be audited
    /// cleanly inside the clear part of the screen in another pass; on a screen that does not scroll
    /// nothing is exempt.
    @MainActor
    func auditScreen(file: StaticString = #filePath, line: UInt = #line) throws {
        // One pass. A pass with failures that measured moving content (a scroll, a large title or a
        // bounce still settling) runs once more at rest; a failure at rest fails the test.
        func settledPass(scrolled: Bool) throws -> AuditPass {
            var pass = try audit(scrolled: scrolled)
            if pass.moved, !pass.failures.isEmpty {
                print("AUDIT content moved during the pass: again")
                _ = stableFrames()
                pass = try audit(scrolled: scrolled)
            }
            for failure in pass.failures { XCTFail("Accessibility audit: \(failure)", file: file, line: line) }
            if !pass.failures.isEmpty {
                XCTContext.runActivity(named: "Audit failure: the screen at rest") { activity in
                    let shot = XCTAttachment(screenshot: screenshot())
                    shot.lifetime = .keepAlways
                    activity.add(shot)
                }
            }
            return pass
        }
        Thread.sleep(forTimeInterval: 1)  // a launch read or a push has settled
        _ = stableFrames()
        var passes = [try settledPass(scrolled: false)]
        // Drag until the content no longer scrolls (`scrollShift`: texts that tick or come and go do
        // not count): the last pass shows the end of the page. A page that still scrolls after
        // `maxDrags` fails (its end was never audited).
        let maxDrags = 10
        var reachedEnd = false
        for _ in 0..<maxDrags {
            let before = stableFrames()
            // A drag with a hold: the content rests where the finger stops (no momentum, no bounce).
            let area = windows.firstMatch
            // In the left margin, outside every card: a press opens or highlights nothing.
            area.coordinate(withNormalizedOffset: CGVector(dx: 0.02, dy: 0.75)).press(
                forDuration: 0.2, thenDragTo: area.coordinate(withNormalizedOffset: CGVector(dx: 0.02, dy: 0.35)),
                withVelocity: .default, thenHoldForDuration: 0.5)
            // Wait until the content rests (a page that fits bounces back, a large title snaps), as
            // before the top pass, then see whether it moved.
            Thread.sleep(forTimeInterval: 1)
            if scrollShift(from: before, to: stableFrames()) == nil {
                reachedEnd = true
                break
            }
            passes.append(try settledPass(scrolled: true))
        }
        if !reachedEnd { XCTFail("the page still scrolls after \(maxDrags) drags", file: file, line: line) }
        let clean = passes.reduce(into: Set<String>()) { $0.formUnion($1.clean) }
        // Measured on Lighting (the longest page): 8 such issues in one pass. More would hide real
        // text without an element.
        for p in passes where p.cutGlyphs > 8 {
            XCTFail("\(p.cutGlyphs) element-less text issues in one scrolled pass", file: file, line: line)
        }
        for key in passes.flatMap(\.exempt) where !clean.contains(key) {
            XCTFail("\(key): exempt, but never audited inside the clear screen", file: file, line: line)
        }
        // Back to the top for the caller.
        for _ in 0..<passes.count { swipeDown() }
    }

    /// `frames()` once the content did not scroll between two reads 0.3 s apart (at most 3 s). A text
    /// that ticks or a banner does not keep it waiting (`scrollShift`).
    @MainActor
    private func stableFrames() -> [String: CGRect] {
        var last = frames()
        for _ in 0..<10 {
            Thread.sleep(forTimeInterval: 0.3)
            let now = frames()
            if scrollShift(from: last, to: now) == nil { return now }
            last = now
        }
        return last
    }

    /// The frames of the labelled elements, keyed "name#n" (the n-th element with that name in tree
    /// order), so two rows with one name ("Background lighting") are two keys. The name is the
    /// element's accessibility identifier if it has one (the pill, whose label ticks with the age:
    /// "as of 1 min ago", then "2 min ago"), else its label. One snapshot: also a cheap check
    /// whether the content moved.
    @MainActor
    private func frames() -> [String: CGRect] {
        var out: [String: CGRect] = [:]
        var seen: [String: Int] = [:]
        func walk(_ e: XCUIElementSnapshot) {
            if !e.label.isEmpty {
                let name = e.identifier.isEmpty ? e.label : e.identifier
                let n = seen[name, default: 0]
                seen[name] = n + 1
                out["\(name)#\(n)"] = e.frame
            }
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
    /// issue with no element, and only when a labelled element crosses the navigation bar's lower
    /// edge in that pass (a row really is cut there; at most 8 per pass). Each exemption is attached
    /// to the result (kept on success). Every other issue goes into `failures`, and `moved` tells
    /// whether the content moved during the pass; `auditScreen` fails on them.
    @MainActor
    func audit(scrolled: Bool) throws -> AuditPass {
        let screen = windows.firstMatch.frame
        let top = scrolled && navigationBars.firstMatch.exists ? navigationBars.firstMatch.frame.maxY + 44 : screen.minY
        let bottom = tabBars.firstMatch.exists ? tabBars.firstMatch.frame.minY - 44 : screen.maxY
        let clear = CGRect(x: screen.minX, y: top, width: screen.width, height: max(0, bottom - top))
        print(
            "AUDIT pass on \(navigationBars.firstMatch.identifier) scrolled=\(scrolled) clear=\(clear)"
                + " navigationBar=\(navigationBars.firstMatch.frame) tabBar=\(tabBars.firstMatch.frame)")
        var pass = AuditPass()
        let all = frames()
        let edge = navigationBars.firstMatch.exists ? navigationBars.firstMatch.frame.maxY : screen.minY
        let rowCut = scrolled && all.values.contains { $0.minY < edge && $0.maxY > edge }
        // XCTest's own audit can time out on a loaded simulator (not a finding): once more, then fail.
        for attempt in 1...2 {
            pass = AuditPass()
            pass.clean = Set(all.filter { clear.contains($0.value) }.keys)
            do {
                try performAccessibilityAudit { issue in
                    let label =
                        issue.element.map { el in
                            elementKey(name: el.identifier.isEmpty ? el.label : el.identifier, frame: el.frame, in: all)
                        } ?? "-"
                    pass.clean.remove(label)
                    // After a scroll, a row cut at the hard edge under the navigation bar leaves glyph pieces
                    // with no element ("potentially inaccessible text"); the same rows are elements in the
                    // top pass.
                    let cutGlyphs = rowCut && issue.element == nil && issue.auditType == .elementDetection
                    if cutGlyphs { pass.cutGlyphs += 1 }
                    let outside =
                        cutGlyphs
                        || issue.element.map { !clear.contains($0.frame) } ?? false
                            && [.contrast, .dynamicType, .textClipped].contains(issue.auditType)
                    let frame = issue.element.map { "\($0.frame)" } ?? ""
                    let exempt = outside ? "exempt (outside the clear screen) " : ""
                    print("AUDIT \(exempt)\(issue.compactDescription) | \(label) \(frame) | \(issue.detailedDescription)")
                    if outside {
                        if !cutGlyphs { pass.exempt.append(label) }
                        let name = "Audit exemption (outside the clear screen): \(issue.compactDescription)"
                        XCTContext.runActivity(named: name) { activity in
                            let attachment = XCTAttachment(string: "\(label)\n\(issue.detailedDescription)")
                            attachment.lifetime = .keepAlways
                            activity.add(attachment)
                        }
                    }
                    // Decided after the pass (`auditScreen`): a pass that measured moving content runs again.
                    if !outside {
                        pass.failures.append("\(issue.compactDescription) | \(label) \(frame) | \(issue.detailedDescription)")
                    }
                    return true
                }
                break
            } catch let error as NSError
                where error.domain == "com.apple.xcode.xctest.accessibilityAudit" && error.code == -56 && attempt == 1
            {
                print("AUDIT the audit did not complete in time (\(error.code)): again")
            }
        }
        pass.moved = frames() != all
        return pass
    }
}

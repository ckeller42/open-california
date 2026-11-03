import XCTest

/// The bench lab: one control action or link check per run, on a real iPhone that is paired with
/// the fake camper unit (`LabPairingUITests`). The runner names the action in `LAB_ACTION`
/// (`TEST_RUNNER_LAB_ACTION=cooler.power.off xcodebuild test-without-building …`), so a driver can
/// mark each action in the fake unit's record and `tools/capture_diff.py --recording` can check the
/// app's frame byte for byte. Without `LAB_ACTION` the test skips: CI and the simulator never run it.
///
/// Actions:
/// - `<function>.<what>.<on|off>` for a switch (camping mode, its lights and USB, the refrigerator
///   box, all lights, immediate heating; `airheater.permanent.off`),
/// - `<function>.<what>.<value>` for a menu: `cooler.level.<1-5>`, `cooler.mode.<normal|quiet|timer_quiet>`,
///   `airheater.level.<1-10>`, `airheater.runtime.<10-120>`, `energy.mode.<normal|max_charge|eco>`, and
///   `lighting.<lamp>.<0-10>` for each lamp (`reading-1`, `kitchen`, `entrance`, …),
/// - `lighting.profile.<1|5|6|7>` for a favourite,
/// - `link.live` (wait for a live link, tapping "Tap to connect" when it shows) and `link.lost` (the
///   link must leave "live" once the fake unit is gone),
/// - `setup.forget.x`, `link.unpaired.x`, `setup.repair.x` (forget the unit, a fresh start that must stay
///   unpaired, and pairing again through the guide), `setup.trace.<on|off>` (the Bluetooth trace switch),
/// - `device.autolock.<choice>` (sets the iPhone's Auto-Lock, so a long lab run is not stopped by a
///   locked iPhone) and `device.autolock.read` (prints `LAB_AUTOLOCK=<value>`, so the driver can
///   restore the owner's value at the end).
final class LabControlUITests: XCTestCase {
    /// Where an action's control is: a cockpit tile or a tab.
    enum Screen {
        case tile(String)
        case tab(String)
    }

    /// The cockpit tile and the switch label of each switch action.
    private static let switches: [String: (screen: Screen, label: String)] = [
        "campingmode.master": (.tile("Camping mode"), "Camping mode"),
        "campingmode.lights": (.tile("Camping mode"), "Exterior and interior lighting"),
        "campingmode.usb": (.tile("Camping mode"), "Rear USB ports"),
        "cooler.power": (.tile("Cooler"), "Refrigerator box"),
        "lighting.power": (.tile("Lighting"), "All lights"),
        "airheater.power": (.tile("Air heater"), "Immediate heating"),
        "airheater.permanent": (.tile("Air heater"), "Continuous heating"),
    ]
    private static let favourites = ["1": "A", "5": "B", "6": "C", "7": "D"]
    /// Each lamp's menu, by its control key: VoiceOver's name for it (group, lamp).
    private static let lamps = [
        "reading-1": "Reading lights, Left", "reading-2": "Reading lights, Right",
        "reading-3": "Reading lights, Front passenger", "kitchen-ambient": "Kitchen, Background lighting",
        "kitchen-cabinet": "Kitchen, Cabinet", "kitchen": "Kitchen, Cooking",
        "roof-ambient": "Pop-up roof, Background lighting", "roof-reading": "Pop-up roof, Reading light",
        "outside-rear": "Exterior light, Rear surroundings", "entrance": "Exterior light, Entrance",
    ]

    /// The screen, the menu's name and the option's text of a menu action; nil for any other action.
    private static func menu(_ fn: String, _ what: String, _ value: String) -> (Screen, String, String)? {
        let nbsp = "\u{00A0}"
        switch (fn, what) {
        case ("cooler", "level"): return (.tile("Cooler"), "Cooling level", value)
        case ("cooler", "mode"):
            let labels = ["normal": "Off", "quiet": "Manual", "timer_quiet": "Automatic"]
            return labels[value].map { (.tile("Cooler"), "Quiet mode", $0) }
        case ("airheater", "level"): return (.tile("Air heater"), "Heating temperature", value == "10" ? "HI" : "Level \(value)")
        case ("airheater", "runtime"): return (.tile("Air heater"), "Run time", "\(value)\(nbsp)min")
        case ("energy", "mode"):
            let labels = ["normal": "Normal", "max_charge": "Max", "eco": "ECO"]
            return labels[value].map { (.tab("Energy"), "Energy mode", $0) }
        case ("lighting", _):
            return lamps[what].map { (.tile("Lighting"), $0, value == "0" ? "Off" : value) }
        default: return nil
        }
    }

    @MainActor
    private func open(_ app: XCUIApplication, _ screen: Screen) {
        switch screen {
        case .tile(let name):
            let tile = app.buttons[name]
            XCTAssertTrue(tile.waitForExistence(timeout: 10), name)
            if !tile.isHittable { app.swipeUp() }
            tile.tap()
        case .tab(let name):
            app.tabBars.buttons[name].tap()
        }
    }

    /// Scrolls until `element` can be tapped (at most six swipes).
    @MainActor
    private func reveal(_ app: XCUIApplication, _ element: XCUIElement) -> Bool {
        guard element.exists else { return false }
        for _ in 0..<6 {
            if element.exists && element.isHittable { return true }
            app.swipeUp()
        }
        return element.exists && element.isHittable
    }

    /// Taps "Continue" when the app asks for a confirmation.
    @MainActor
    private func confirmIfAsked(_ app: XCUIApplication) {
        let confirm = app.alerts.buttons["Continue"]
        if confirm.waitForExistence(timeout: 3) {
            shot(app, "confirm")
            confirm.tap()
        }
    }

    @MainActor
    private func shot(_ app: XCUIApplication, _ name: String) {
        let a = XCTAttachment(screenshot: app.screenshot())
        a.name = name
        a.lifetime = .keepAlways
        add(a)
    }

    @MainActor
    private func waitLive(_ app: XCUIApplication, _ timeout: TimeInterval) -> Bool {
        let live = app.staticTexts["live"]
        let deadline = Date().addingTimeInterval(timeout)
        while Date() < deadline {
            if live.exists { return true }
            let connect = app.buttons.matching(NSPredicate(format: "label BEGINSWITH 'Tap to connect'")).firstMatch
            if connect.exists && connect.isHittable { connect.tap() }
            _ = live.waitForExistence(timeout: 2)
        }
        return live.exists
    }

    /// `device.autolock.<choice>`: Settings → Display & Brightness → Auto-Lock → the choice (English
    /// or German Settings), so a lab run is not stopped by a locked iPhone. `<choice>` is the row's
    /// label, for example `Never`/`Nie` or `30 Seconds`.
    /// `read` prints the current value as `LAB_AUTOLOCK=<value>` and changes nothing.
    @MainActor
    private func setAutoLock(_ choice: String) throws {
        let settings = XCUIApplication(bundleIdentifier: "com.apple.Preferences")
        settings.launch()
        func tapFirst(_ labels: [String]) -> Bool {
            for _ in 0..<6 {
                for label in labels {
                    let cell = settings.staticTexts[label].firstMatch
                    if cell.exists && cell.isHittable {
                        cell.tap()
                        return true
                    }
                }
                settings.swipeUp()
            }
            return false
        }
        // Back to the root of Settings first.
        while settings.navigationBars.buttons.element(boundBy: 0).exists
            && settings.navigationBars.buttons.element(boundBy: 0).isHittable
        {
            settings.navigationBars.buttons.element(boundBy: 0).tap()
        }
        XCTAssertTrue(tapFirst(["Display & Brightness", "Anzeige & Helligkeit"]), "Display & Brightness")
        let rowNames = ["Auto-Lock", "Automatische Sperre"]
        if choice == "read" {
            // The row shows its name and the current value; the driver keeps the value to restore it.
            let row = settings.cells.containing(
                NSPredicate(format: "label IN %@", rowNames)
            ).firstMatch
            XCTAssertTrue(row.waitForExistence(timeout: 5), "Auto-Lock row")
            let texts = row.staticTexts.allElementsBoundByIndex.map(\.label).filter { !rowNames.contains($0) }
            let value = texts.first ?? (row.value as? String ?? "")
            print("LAB_AUTOLOCK=\(value)")
            shot(settings, "auto-lock-read")
            settings.terminate()
            return
        }
        XCTAssertTrue(tapFirst(rowNames), "Auto-Lock")
        let names = choice == "Never" || choice == "Nie" ? ["Never", "Nie"] : [choice]
        XCTAssertTrue(tapFirst(names), choice)
        shot(settings, "auto-lock")
        settings.terminate()
    }

    /// Picks `option` in the menu `name`, then waits until the menu shows it (the unit's feedback).
    /// A menu's accessibility label is its name and its value: "Heating temperature, Level 5".
    @MainActor
    private func runMenu(_ app: XCUIApplication, _ screen: Screen, _ name: String, _ option: String) throws {
        open(app, screen)
        let picker = app.buttons.matching(NSPredicate(format: "label BEGINSWITH %@", name + ", ")).firstMatch
        XCTAssertTrue(picker.waitForExistence(timeout: 10), "menu \(name)")
        XCTAssertTrue(reveal(app, picker), "menu \(name) on screen")
        shot(app, "before")
        let chosen = name + ", " + option
        if picker.label == chosen {
            throw XCTSkip("\(name) is already \(option)")
        }
        picker.tap()
        let item = app.collectionViews.buttons[option].firstMatch
        let choice = item.waitForExistence(timeout: 5) ? item : app.buttons[option].firstMatch
        XCTAssertTrue(choice.waitForExistence(timeout: 5), "menu item \(option)")
        shot(app, "menu")
        choice.tap()
        confirmIfAsked(app)
        let applied = app.buttons.matching(NSPredicate(format: "label == %@", chosen)).firstMatch
        XCTAssertTrue(applied.waitForExistence(timeout: 20), "menu shows \(option)")
    }

    /// Turns the switch `label` on or off, then waits until it shows the new state.
    @MainActor
    private func runSwitch(_ app: XCUIApplication, _ screen: Screen, _ label: String, on: Bool) throws {
        let target = on ? "1" : "0"
        open(app, screen)
        let toggle = app.switches[label]
        XCTAssertTrue(toggle.waitForExistence(timeout: 10), "switch \(label)")
        XCTAssertTrue(reveal(app, toggle), "switch \(label) on screen")
        shot(app, "before")
        if toggle.value as? String == target {
            throw XCTSkip("\(label) is already \(on ? "on" : "off")")
        }
        // The switch's own hit point is its right end; a tap on the row's middle misses it.
        toggle.coordinate(withNormalizedOffset: CGVector(dx: 0.9, dy: 0.5)).tap()
        confirmIfAsked(app)
        expectation(for: NSPredicate(format: "value == %@", target), evaluatedWith: toggle)
        waitForExpectations(timeout: 20)
    }

    /// `setup.forget`: Setup → "Forget this camper unit…" → "Forget"; the guide then says the app has
    /// forgotten the unit and the pill "not paired". `setup.repair`: "Pair again…" → the guide → the
    /// unit (iOS still holds the bond, so no passkey) → a live link again.
    @MainActor
    private func runSetup(_ app: XCUIApplication, _ step: String) throws {
        app.tabBars.buttons["Setup"].tap()
        if step == "forget" {
            XCTAssertTrue(waitLive(app, 30), "a live link before the forget")
            let forget = app.buttons["Forget this camper unit…"]
            XCTAssertTrue(forget.waitForExistence(timeout: 10), "the forget button")
            forget.tap()  // a tap scrolls the button into view
            let confirm = app.alerts.buttons["Forget"]
            XCTAssertTrue(confirm.waitForExistence(timeout: 5), "the confirmation")
            shot(app, "forget-confirm")
            confirm.tap()
            XCTAssertTrue(app.staticTexts["The app has forgotten the camper unit"].waitForExistence(timeout: 10))
            XCTAssertTrue(app.staticTexts["not paired"].waitForExistence(timeout: 10), "not paired")
            shot(app, "forgotten")
            return
        }
        // Right after a forget the guide offers "Pair again…"; after a fresh start it opens at step 1.
        let again = app.buttons["Pair again…"]
        if again.waitForExistence(timeout: 5) { again.tap() }
        let open = app.buttons["I'm on that screen"]
        XCTAssertTrue(open.waitForExistence(timeout: 10), "the guide's first step")
        open.tap()
        let unit = app.buttons.matching(NSPredicate(format: "label CONTAINS 'VWCAMPER'")).firstMatch
        if unit.waitForExistence(timeout: 20) { unit.tap() }
        let close = app.buttons["Close"]
        XCTAssertTrue(close.waitForExistence(timeout: 60), "the guide's done step")
        shot(app, "repaired")
        close.tap()
        XCTAssertTrue(waitLive(app, 30), "a live link after pairing again")
    }

    @MainActor
    func testLabAction() throws {
        guard let action = ProcessInfo.processInfo.environment["LAB_ACTION"], !action.isEmpty else {
            throw XCTSkip("lab only: set TEST_RUNNER_LAB_ACTION")
        }
        // `link.live` and `link.lost` have no value part.
        let parts = (action.split(separator: ".").map(String.init) + [""]).prefix(3).map { $0 }
        XCTAssertTrue(action.split(separator: ".").count >= 2, "action \(action)")
        if parts[0] == "device" {
            try setAutoLock(parts[2])
            return
        }
        let app = XCUIApplication()
        app.launchArguments = ["-AppleLanguages", "(en)", "-AppleLocale", "en_US"]
        app.launch()

        if parts[0] == "setup" {
            if parts[1] == "trace" {
                // The Bluetooth trace switch on Setup; it acts at once and needs no link.
                app.tabBars.buttons["Setup"].tap()
                try runSwitch(app, .tab("Setup"), "Record Bluetooth trace", on: parts[2] == "on")
                return
            }
            try runSetup(app, parts[1])
            return
        }
        if parts[0] == "link" {
            if parts[1] == "unpaired" {
                // A fresh start after a forget: the app must not reach the old unit by itself.
                XCTAssertTrue(app.staticTexts["not paired"].waitForExistence(timeout: 15), "not paired")
                sleep(15)
                XCTAssertFalse(app.staticTexts["live"].exists, "no link to the forgotten unit")
                shot(app, "link-unpaired")
                return
            }
            if parts[1] == "lost" {
                let live = app.staticTexts["live"]
                let gone = NSPredicate(format: "exists == false")
                expectation(for: gone, evaluatedWith: live)
                waitForExpectations(timeout: 60)
                shot(app, "link-lost")
            } else {
                XCTAssertTrue(waitLive(app, 60), "a live link")
                shot(app, "link-live")
            }
            return
        }

        XCTAssertTrue(waitLive(app, 30), "a live link before the action")
        let (fn, what, value) = (parts[0], parts[1], parts[2])
        if what == "profile" {
            let fav = try XCTUnwrap(Self.favourites[value], "favourite \(value)")
            open(app, .tile("Lighting"))
            let button = app.buttons["Favourite \(fav)"]
            XCTAssertTrue(button.waitForExistence(timeout: 10), "favourite \(fav)")
            shot(app, "before")
            button.tap()
        } else if let (screen, name, option) = Self.menu(fn, what, value) {
            try runMenu(app, screen, name, option)
        } else {
            let row = try XCTUnwrap(Self.switches[fn + "." + what], "no UI path for \(action)")
            try runSwitch(app, row.screen, row.label, on: value == "on")
        }
        sleep(2)  // the unit's feedback notification lands in the record before the next action
        shot(app, "after")
    }
}

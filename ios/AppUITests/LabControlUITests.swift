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
/// - `device.autolock.<choice>` (sets the iPhone's Auto-Lock, so a long lab run is not stopped by a
///   locked iPhone).
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
        XCTAssertTrue(tapFirst(["Auto-Lock", "Automatische Sperre"]), "Auto-Lock")
        let names = choice == "Never" || choice == "Nie" ? ["Never", "Nie"] : [choice]
        XCTAssertTrue(tapFirst(names), choice)
        shot(settings, "auto-lock")
        settings.terminate()
    }

    /// Picks `option` in the menu `name`, then waits until the menu shows it (the unit's feedback).
    @MainActor
    private func runMenu(_ app: XCUIApplication, _ screen: Screen, _ name: String, _ option: String) throws {
        open(app, screen)
        let picker = app.buttons[name]
        XCTAssertTrue(picker.waitForExistence(timeout: 10), "menu \(name)")
        XCTAssertTrue(reveal(app, picker), "menu \(name) on screen")
        shot(app, "before")
        if picker.value as? String == option {
            throw XCTSkip("\(name) is already \(option)")
        }
        picker.tap()
        let item = app.collectionViews.buttons[option].firstMatch
        let choice = item.waitForExistence(timeout: 5) ? item : app.buttons[option].firstMatch
        XCTAssertTrue(choice.waitForExistence(timeout: 5), "menu item \(option)")
        shot(app, "menu")
        choice.tap()
        confirmIfAsked(app)
        expectation(for: NSPredicate(format: "value == %@", option), evaluatedWith: picker)
        waitForExpectations(timeout: 20)
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

    @MainActor
    func testLabAction() throws {
        guard let action = ProcessInfo.processInfo.environment["LAB_ACTION"], !action.isEmpty else {
            throw XCTSkip("lab only: set TEST_RUNNER_LAB_ACTION")
        }
        let parts = action.split(separator: ".").map(String.init)
        XCTAssertEqual(parts.count, 3, "action \(action)")
        if parts[0] == "device" {
            try setAutoLock(parts[2])
            return
        }
        let app = XCUIApplication()
        app.launchArguments = ["-AppleLanguages", "(en)", "-AppleLocale", "en_US"]
        app.launch()

        if parts[0] == "link" {
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

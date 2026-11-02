import XCTest

/// The bench lab: one control action or link check per run, on a real iPhone that is paired with
/// the fake camper unit (`LabPairingUITests`). The runner names the action in `LAB_ACTION`
/// (`TEST_RUNNER_LAB_ACTION=cooler.power.off xcodebuild test-without-building …`), so a driver can
/// mark each action in the fake unit's record and `tools/capture_diff.py --recording` can check the
/// app's frame byte for byte. Without `LAB_ACTION` the test skips: CI and the simulator never run it.
///
/// Actions: `<function>.<what>.<on|off>` for a switch, `lighting.profile.<1|5|6|7>` for a favourite,
/// `link.live` (wait for a live link, tapping "Tap to connect" when it shows) and `link.lost` (the
/// link must leave "live" once the fake unit is gone), and `device.autolock.<choice>` (sets the
/// iPhone's Auto-Lock, so a long lab run is not stopped by a locked iPhone).
final class LabControlUITests: XCTestCase {
    /// The cockpit tile and the switch label of each switch action.
    private static let switches: [String: (tile: String, label: String)] = [
        "campingmode.master": ("Camping mode", "Camping mode"),
        "campingmode.lights": ("Camping mode", "Exterior and interior lighting"),
        "campingmode.usb": ("Camping mode", "Rear USB ports"),
        "cooler.power": ("Cooler", "Refrigerator box"),
        "lighting.power": ("Lighting", "All lights"),
        "airheater.power": ("Air heater", "Immediate heating"),
    ]
    private static let favourites = ["1": "A", "5": "B", "6": "C", "7": "D"]

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
        let key = parts[0] + "." + parts[1]
        if parts[1] == "profile" {
            let fav = try XCTUnwrap(Self.favourites[parts[2]], "favourite \(parts[2])")
            app.buttons["Lighting"].tap()
            let button = app.buttons["Favourite \(fav)"]
            XCTAssertTrue(button.waitForExistence(timeout: 10), "favourite \(fav)")
            shot(app, "before")
            button.tap()
        } else {
            let row = try XCTUnwrap(Self.switches[key], "no UI path for \(action)")
            let target = parts[2] == "on" ? "1" : "0"
            app.buttons[row.tile].tap()
            let toggle = app.switches[row.label]
            XCTAssertTrue(toggle.waitForExistence(timeout: 10), "switch \(row.label)")
            shot(app, "before")
            if toggle.value as? String == target {
                throw XCTSkip("\(row.label) is already \(parts[2])")
            }
            // The switch's own hit point is its right end; a tap on the row's middle misses it.
            toggle.coordinate(withNormalizedOffset: CGVector(dx: 0.9, dy: 0.5)).tap()
            let confirm = app.alerts.buttons["Continue"]
            if confirm.waitForExistence(timeout: 3) {
                shot(app, "confirm")
                confirm.tap()
            }
            let applied = NSPredicate(format: "value == %@", target)
            expectation(for: applied, evaluatedWith: toggle)
            waitForExpectations(timeout: 20)
        }
        sleep(2)  // the unit's feedback notification lands in the record before the next action
        shot(app, "after")
    }
}

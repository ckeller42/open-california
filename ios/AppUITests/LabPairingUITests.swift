import XCTest

/// The bench lab: the app on a real iPhone pairs with the fake camper unit
/// (`tools/applab/fake_unit_ble.py` with a pinned `FAKE_UNIT_PASSKEY`). Runs only when the test
/// runner gets `LAB_PASSKEY` (`TEST_RUNNER_LAB_PASSKEY=… xcodebuild test …`); CI and the simulator
/// skip it. Each step attaches a screenshot to the result bundle.
final class LabPairingUITests: XCTestCase {
    @MainActor
    private func shot(_ app: XCUIApplication, _ name: String) {
        let a = XCTAttachment(screenshot: app.screenshot())
        a.name = name
        a.lifetime = .keepAlways
        add(a)
    }

    /// Taps the first button of the system alert whose label is one of `labels`.
    @MainActor
    private func tapAlert(_ springboard: XCUIApplication, _ labels: [String]) -> Bool {
        let alert = springboard.alerts.firstMatch
        for label in labels where alert.buttons[label].exists {
            alert.buttons[label].tap()
            return true
        }
        return false
    }

    @MainActor
    func testPairWithTheFakeUnit() throws {
        guard let passkey = ProcessInfo.processInfo.environment["LAB_PASSKEY"], !passkey.isEmpty else {
            throw XCTSkip("lab only: set TEST_RUNNER_LAB_PASSKEY")
        }
        let springboard = XCUIApplication(bundleIdentifier: "com.apple.springboard")
        let app = XCUIApplication()
        app.launchArguments = ["-AppleLanguages", "(en)", "-AppleLocale", "en_US"]
        app.launch()
        shot(app, "1-launch")

        // A first launch asks for Bluetooth.
        if springboard.alerts.firstMatch.waitForExistence(timeout: 3) {
            _ = tapAlert(springboard, ["Allow", "Erlauben", "OK"])
        }
        // The pairing guide is on the Setup tab.
        let setup = app.tabBars.buttons["Setup"]
        if setup.waitForExistence(timeout: 5) { setup.tap() }
        let again = app.buttons["Pair again…"]
        if again.waitForExistence(timeout: 5) { again.tap() }
        let open = app.buttons["I'm on that screen"]
        XCTAssertTrue(open.waitForExistence(timeout: 15), "the pairing guide's first step")
        shot(app, "2-guide")
        open.tap()

        // Several units: the pick list; the fake is VWCAMPER.
        let unit = app.buttons.matching(NSPredicate(format: "label CONTAINS 'VWCAMPER'")).firstMatch
        if unit.waitForExistence(timeout: 20) {
            shot(app, "3-pick")
            unit.tap()
        }

        // iOS asks for the passkey in its own alert.
        let alert = springboard.alerts.firstMatch
        XCTAssertTrue(alert.waitForExistence(timeout: 30), "the system pairing alert")
        shot(springboard, "4-passkey-alert")
        let field = alert.textFields.count > 0 ? alert.textFields.firstMatch : alert.secureTextFields.firstMatch
        field.tap()
        field.typeText(passkey)
        XCTAssertTrue(tapAlert(springboard, ["Pair", "Koppeln"]), "the alert's pair button")

        let close = app.buttons["Close"]
        XCTAssertTrue(close.waitForExistence(timeout: 30), "the guide's done step")
        shot(app, "5-paired")
        close.tap()
        XCTAssertTrue(app.staticTexts["live"].waitForExistence(timeout: 30), "a live link after pairing")
        shot(app, "6-live")
    }
}

import XCTest

/// The app starts on a fixture (`-UITestFixture <name>`): the fake transport, never real Bluetooth.
final class LaunchUITests: XCTestCase {
    /// - Test: T_IOS_UI_LAUNCH_FIXTURE
    /// - Verifies: R_IOS_TESTED, R_IOS_I18N
    @MainActor
    func testParkedFixtureComesUpLive() throws {
        let app = XCUIApplication()
        app.launchArguments = ["-UITestFixture", "parked", "-AppleLanguages", "(en)"]
        app.launch()
        XCTAssertTrue(app.staticTexts["live"].waitForExistence(timeout: 10))
        try app.audit()
    }

    /// - Test: T_IOS_UI_LAUNCH_GERMAN
    /// - Verifies: R_IOS_I18N
    @MainActor
    func testUnsupportedFixtureSaysSoInGerman() throws {
        let app = XCUIApplication()
        app.launchArguments = ["-UITestFixture", "unsupportedV3", "-AppleLanguages", "(de)", "-AppleLocale", "de_DE"]
        app.launch()
        XCTAssertTrue(app.staticTexts["Diese Camper-Einheit wird noch nicht unterstützt"].waitForExistence(timeout: 10))
        try app.audit()
    }
}

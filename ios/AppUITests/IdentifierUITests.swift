import XCTest

/// Every control has a stable accessibility identifier (`<screen>.<control>`), so a lab test or a
/// bug report can name it without its English label.
final class IdentifierUITests: XCTestCase {
    /// On Cockpit, each control screen, Energy, Water and Setup (the guide collapsed and open),
    /// every button, switch, slider, stepper and menu in the page's content has an identifier.
    ///
    /// - Test: T_IOS_UI_IDENTIFIERS
    /// - Verifies: R_IOS_TESTED
    @MainActor
    func testEveryControlHasAnIdentifier() throws {
        let app = XCUIApplication()
        app.launchArguments = ["-UITestFixture", "campingOn", "-AppleLanguages", "(en)", "-AppleLocale", "en_US"]
        app.launch()
        XCTAssertTrue(app.navigationBars["Cockpit"].waitForExistence(timeout: 10))
        var missing = app.controlsWithoutIdentifier(on: "Cockpit")
        for title in ["Camping mode", "Lighting", "Cooler", "Air heater"] {
            let tile = app.buttons[title]
            XCTAssertTrue(tile.waitForExistence(timeout: 10), title)
            if !tile.isHittable { app.swipeUp() }
            tile.tap()
            XCTAssertTrue(app.navigationBars[title].waitForExistence(timeout: 5), title)
            missing += app.controlsWithoutIdentifier(on: title)
            app.navigationBars[title].buttons.element(boundBy: 0).tap()
            XCTAssertTrue(app.navigationBars["Cockpit"].waitForExistence(timeout: 5))
        }
        for tab in ["Energy", "Water", "Setup"] {
            app.tabBars.buttons[tab].tap()
            XCTAssertTrue(app.navigationBars[tab].waitForExistence(timeout: 5), tab)
            missing += app.controlsWithoutIdentifier(on: tab)
        }
        let pairAgain = app.buttons["setup.pairAgain"]
        XCTAssertTrue(pairAgain.waitForExistence(timeout: 10))
        pairAgain.tap()
        XCTAssertTrue(app.staticTexts["Pair the camper unit"].waitForExistence(timeout: 5))
        missing += app.controlsWithoutIdentifier(on: "Setup, pair panel")
        XCTAssertEqual(missing, [], "controls without an accessibility identifier")
        app.terminate()
    }
}

extension XCUIApplication {
    /// "<screen>: <label>" for each control in the page's scroll view that has no identifier.
    @MainActor
    func controlsWithoutIdentifier(on screen: String) -> [String] {
        Thread.sleep(forTimeInterval: 1)  // the push or the tab switch has settled
        guard let root = try? snapshot() else { return ["\(screen): no snapshot"] }
        let controls: Set<XCUIElement.ElementType> = [.button, .switch, .toggle, .slider, .stepper, .popUpButton, .menuButton]
        func scroller(_ e: XCUIElementSnapshot) -> XCUIElementSnapshot? {
            if [.scrollView, .collectionView, .table].contains(e.elementType) { return e }
            return e.children.lazy.compactMap(scroller).first
        }
        var out: [String] = []
        func walk(_ e: XCUIElementSnapshot) {
            if controls.contains(e.elementType), e.identifier.isEmpty { out.append("\(screen): \(e.label)") }
            e.children.forEach(walk)
        }
        guard let page = scroller(root) else { return ["\(screen): no scroll view"] }
        walk(page)
        return out
    }
}

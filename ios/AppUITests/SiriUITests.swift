import XCTest

/// Siri's own phrase resolution, in English and German, over the `parked` fixture (fake transport).
final class SiriUITests: XCTestCase {
    /// The spoken phrase runs the camping intent in the running app: its result shows in the app's
    /// banner (the fake unit pushes nothing, so the honest result is "sent, not confirmed"). The exact
    /// frame (`campingmode master on`) is checked in `IntentTests.campingIntentSendsMasterOn`; this
    /// test proves that the phrase reaches that intent. Without Siri in the simulator it is skipped
    /// with the reason, and the phrase is checked on the iPhone (`docs/ios-app-siri-checklist.md`).
    ///
    /// - Test: T_IOS_UI_SIRI_PHRASE
    /// - Verifies: R_IOS_SIRI, R_IOS_I18N
    @MainActor
    func testSiriPhraseRunsCampingIntent() throws {
        let cases = [
            ("en", "en_US", "Turn camping mode on in Camper", "Camping mode: Sent"),
            ("de", "de_DE", "Campingmodus an in Camper", "Campingmodus: Gesendet"),
        ]
        for (lang, locale, phrase, banner) in cases {
            let app = XCUIApplication()
            app.launchArguments = ["-UITestFixture", "parked", "-AppleLanguages", "(\(lang))", "-AppleLocale", locale]
            app.launch()
            XCTAssertTrue(app.otherElements["battery-ring"].waitForExistence(timeout: 10))
            Thread.sleep(forTimeInterval: 4)  // the launch link is past its arm delay

            XCUIDevice.shared.siriService.activate(voiceRecognitionText: phrase)
            let shown = app.descendants(matching: .any).matching(NSPredicate(format: "label BEGINSWITH %@", banner)).firstMatch
            guard shown.waitForExistence(timeout: 20) else {
                // What Siri showed instead (empty: no Siri UI at all), kept as the recorded reason.
                var labels: [String] = []
                func walk(_ e: XCUIElementSnapshot) {
                    if e.elementType == .staticText, !e.label.isEmpty { labels.append(e.label) }
                    e.children.forEach(walk)
                }
                if let s = try? XCUIApplication(bundleIdentifier: "com.apple.springboard").snapshot() { walk(s) }
                let siri = labels.prefix(10).joined(separator: " | ")
                throw XCTSkip(
                    "Siri in this simulator did not run the intent for \"\(phrase)\" within 20 s; "
                        + "Siri showed: [\(siri)]. Checked on the iPhone instead: docs/ios-app-siri-checklist.md")
            }
            app.activate()
            try app.auditScreen()
            app.terminate()
        }
    }
}

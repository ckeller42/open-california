import XCTest

/// The audit's stop rule (`scrollShift`): a scroll is many elements moving together; texts that
/// change, elements that come and go, jitter and one moving banner are not. No app launch.
final class ScrollShiftTests: XCTestCase {
    private func row(_ y: CGFloat) -> CGRect { CGRect(x: 16, y: y, width: 300, height: 32) }

    /// - Test: T_IOS_UI_SCROLL_SHIFT
    /// - Verifies: R_IOS_TESTED
    func testOnlyAScrollOfTheContentCounts() {
        let bars = [
            "Setup#0": CGRect(x: 0, y: 62, width: 402, height: 54), "Cockpit#0": CGRect(x: 20, y: 800, width: 80, height: 40),
        ]
        var top = bars
        for n in 0..<6 { top["Row \(n)#0"] = row(200 + CGFloat(n) * 40) }

        // The content scrolled up by 350 pt; the bars stayed.
        var scrolled = bars
        for n in 0..<6 { scrolled["Row \(n)#0"] = row(200 + CGFloat(n) * 40 - 350) }
        XCTAssertEqual(scrollShift(from: top, to: scrolled), -350)

        // The end of the page: nothing moved, but an age ticked, a banner came, and a row jittered.
        var end = top
        end["as of 1 min ago#0"] = row(120)
        end["Cooler on#0"] = row(700)
        end["Row 0#0"] = row(200.4)
        var later = end
        later["as of 1 min ago#0"] = nil
        later["as of 2 min ago#0"] = row(120)
        later["Cooler on#0"] = nil
        later["Row 0#0"] = row(200)
        XCTAssertNil(scrollShift(from: end, to: later))

        // One banner slides by itself: not a scroll.
        var banner = top
        banner["Sent#0"] = row(760)
        var slid = banner
        slid["Sent#0"] = row(700)
        XCTAssertNil(scrollShift(from: banner, to: slid))

        // A bounce of 2 pt is still a move of the content.
        var bounced = bars
        for n in 0..<6 { bounced["Row \(n)#0"] = row(202 + CGFloat(n) * 40) }
        XCTAssertEqual(scrollShift(from: top, to: bounced), 2)
    }

    /// An issue's element: by name, the nearest of two; a text inside the pill books to the pill.
    /// - Test: T_IOS_UI_AUDIT_ELEMENT_KEY
    /// - Verifies: R_IOS_TESTED
    func testAnIssueBooksToItsElementOrItsHolder() {
        let frames = [
            "Background lighting#0": row(200), "Background lighting#1": row(400),
            "path-pill#0": CGRect(x: 100, y: 120, width: 200, height: 30), "Setup#0": CGRect(x: 0, y: 0, width: 402, height: 874),
        ]
        XCTAssertEqual(elementKey(name: "Background lighting", frame: row(398), in: frames), "Background lighting#1")
        XCTAssertEqual(
            elementKey(name: "offline", frame: CGRect(x: 130, y: 125, width: 50, height: 20), in: frames), "path-pill#0")
        XCTAssertEqual(elementKey(name: "gone", frame: CGRect(x: 500, y: 900, width: 5, height: 5), in: frames), "gone#?")
    }
}

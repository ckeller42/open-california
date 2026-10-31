import CaliBLE
import CaliCore
import Foundation
import Testing

@testable import Camper

@MainActor
struct CamperStoreTests {
    /// Polls `cond` on the main actor until it holds (the session works on real time here).
    private func until(_ what: String, _ cond: () -> Bool) async throws {
        for _ in 0..<200 {
            if cond() { return }
            try await Task.sleep(for: .milliseconds(25))
        }
        Issue.record("timed out waiting for \(what)")
    }

    /// Review Focus 4: a relaunch by Bluetooth state restoration shows a state hours old. The link
    /// comes up, but the controls stay disabled until this link has read the unit again.
    ///
    /// - Test: T_IOS_STORE_STALE_RESTORE
    /// - Verifies: R_IOS_HONEST, R_IOS_BACKGROUND_LINK
    @Test func restoredStaleStateDisablesControls() async throws {
        let store = CamperStore.preview(.staleRestore)
        let restoredAt = try #require(store.state.asOf)
        #expect(store.isStale)
        store.link = .up
        #expect(!store.controlsEnabled)

        // The session's first update has no frame yet: the restored state stays, still disabled.
        store.start()
        try await until("first session update") { store.link == .off }  // the session's link, not ours
        #expect(store.state.asOf == restoredAt)
        #expect(!store.controlsEnabled)

        // A fresh read on this link enables the controls.
        await store.connect()
        try await until("fresh read") { store.controlsEnabled }
        #expect(!store.isStale)
    }

    /// Review Focus 5: a unit with CommunicationVersion 3 gives no controls, also after a fresh read.
    ///
    /// - Test: T_IOS_STORE_UNSUPPORTED
    /// - Verifies: R_IOS_V2_ONLY
    @Test func unsupportedGateDisablesControls() async throws {
        let store = CamperStore.preview(.unsupportedV3)
        #expect(store.state.gate == .unsupported(version: 3))
        #expect(store.link == .up && !store.isStale)
        #expect(!store.controlsEnabled)

        store.start()
        await store.connect()
        try await until("live link") { store.link == .up && store.state.asOf != nil }
        #expect(store.state.gate == .unsupported(version: 3))
        #expect(!store.controlsEnabled)
        #expect(await store.send(Command(function: "campingmode", what: "master", value: "on")) == .notSupported)
    }

    /// - Test: T_IOS_STORE_FRESH_READ
    /// - Verifies: R_IOS_HONEST
    @Test func freshReadEnablesControls() async throws {
        let store = CamperStore(session: Fixture.parked.makeSession())
        #expect(store.isStale)  // no frame at all counts as stale
        #expect(!store.controlsEnabled)

        store.start()
        await store.connect()
        try await until("fresh read") { store.controlsEnabled }
        #expect(store.state.gate == .supported)

        // 90 s later (SAT_OFFLINE_S) without a frame, the state is stale again.
        let asOf = try #require(store.state.asOf)
        store.now = { asOf.addingTimeInterval(91) }
        #expect(store.isStale)
        #expect(!store.controlsEnabled)
    }

    /// The runner plans only over frames of the current link: after a reconnect the previous link's
    /// frames are shown, but the controls wait for this link's read pass.
    ///
    /// - Test: T_IOS_STORE_CURRENT_LINK
    /// - Verifies: R_IOS_HONEST
    @Test func controlsFollowTheCurrentLinksFrames() {
        let store = CamperStore.preview(.parked)
        var live = store.state
        #expect(store.controlsEnabled)
        #expect(store.controlEnabled("campingmode"))

        store.apply(live, .off)  // the unit hangs up
        #expect(!store.controlsEnabled)

        var reconnected = live
        reconnected.liveFunctions = []  // link up again, no frame of this link yet
        store.apply(reconnected, .up)
        #expect(!store.isStale)
        #expect(!store.controlsEnabled)
        #expect(!store.controlEnabled("campingmode"))

        live.liveFunctions = ["general"]  // the read pass has reached `general` only
        store.apply(live, .up)
        #expect(store.controlsEnabled)
        #expect(!store.controlEnabled("campingmode"))

        live.liveFunctions.insert("campingmode")
        store.apply(live, .up)
        #expect(store.controlEnabled("campingmode"))
    }

    /// Ruling 21: each update is saved; a relaunch restores the values with their `asOf`, and the
    /// controls stay disabled (no function is live) until this link reads the unit.
    ///
    /// - Test: T_IOS_STORE_PERSIST_RESTORE
    /// - Verifies: R_IOS_BACKGROUND_LINK, R_IOS_HONEST
    @Test func savedStateIsRestoredWithItsAgeAndControlsDisabled() throws {
        let url = FileManager.default.temporaryDirectory.appending(path: "camper-\(UUID().uuidString)/last_state.json")
        defer { try? FileManager.default.removeItem(at: url.deletingLastPathComponent()) }
        let live = CamperStore.preview(.campingOn).state
        let first = CamperStore(session: Fixture.parked.makeSession(), saveURL: url)
        first.apply(live, .up)
        #expect(first.controlsEnabled)

        let restored = try #require(CamperStore.restore(from: url))
        #expect(restored.fields == live.fields)
        #expect(restored.interpreted == live.interpreted)
        #expect(restored.gate == .supported)
        #expect(restored.asOf == live.asOf)
        #expect(restored.liveFunctions.isEmpty && restored.lightConfig.isEmpty)

        let second = CamperStore(session: Fixture.parked.makeSession(), state: restored, link: .up, saveURL: url)
        #expect(second.value("campingmode", "master_on")?.bool == true)
        #expect(!second.controlsEnabled)
        #expect(!second.controlEnabled("campingmode"))
        #expect(CamperStore.restore(from: url.appending(path: "missing")) == nil)
    }

    /// The water guard stores no `water` frame on a cold start; the restored reading stays, marked
    /// as held with its age, never as live.
    ///
    /// - Test: T_IOS_STORE_WATER_HELD
    /// - Verifies: R_IOS_HONEST
    @Test func restoredWaterStaysHeldAfterAColdRead() throws {
        let store = CamperStore.preview(.staleRestore)
        let restoredAt = try #require(store.state.asOf)
        var fresh = CamperStore.preview(.parked).state
        fresh.fields["water"] = nil
        fresh.interpreted["water"] = nil
        store.apply(fresh, .up)
        #expect(store.state.waterHeld)
        #expect(store.state.waterGoodAt == restoredAt)
        #expect(store.value("water", "fresh")?["liters"]?.number == 11)
    }

    /// "as of {age}" and the stale state follow the clock alone (the screens redraw on a
    /// `TimelineView`): the same state turns stale and its age grows as the injected clock moves.
    ///
    /// - Test: T_IOS_STORE_AGE_CLOCK
    /// - Verifies: R_IOS_HONEST, R_IOS_I18N
    @Test func ageAndStalenessFollowTheClock() throws {
        let t0 = Date(timeIntervalSince1970: 1_800_000_000)
        let store = CamperStore.preview(.parked, now: t0)
        var now = t0
        store.now = { now }
        #expect(!store.isStale)
        #expect(String(localized: try #require(store.age)) == "just now")
        now = t0.addingTimeInterval(91)
        #expect(store.isStale && !store.controlsEnabled)
        #expect(String(localized: try #require(store.age)) == "2\u{00A0}min ago")
        now = t0.addingTimeInterval(5 * 3600)
        #expect(String(localized: try #require(store.age)) == "5\u{00A0}h ago")
        #expect(String(localized: ageText(3 * 86400)) == "3\u{00A0}d ago")
        #expect(String(localized: ageText(-5)) == "just now")
        // VoiceOver hears full words, with the same rounding.
        #expect(spokenAge(5 * 3600, Locale(identifier: "en_US")) == "5 hours ago")
        #expect(spokenAge(5 * 3600, Locale(identifier: "de_DE")) == "vor 5 Stunden")
        #expect(spokenAge(3 * 86400, Locale(identifier: "de_DE")) == "vor 3 Tagen")
        #expect(spoken(14.1, UnitElectricPotentialDifference.volts, Locale(identifier: "en_US")) == "14.1 volts")
    }

    /// A function the unit reports as not installed has no tile.
    ///
    /// - Test: T_IOS_STORE_INSTALLED
    /// - Verifies: R_IOS_INSTALLED
    @Test func installedFollowsTheUnit() {
        #expect(CamperStore.preview(.parked).installed("cooler"))
        #expect(!CamperStore.preview(.noCooler).installed("cooler"))
        #expect(CamperStore.preview(.noCooler).installed("campingmode"))
    }

    /// Every key of the app's string catalogs has an English and a German value. Where the web UI
    /// has the same English string, the German value is the one of `strings.de.js`.
    ///
    /// - Test: T_IOS_STRINGS_EN_DE
    /// - Verifies: R_IOS_I18N
    @Test func stringsHaveEnglishAndGermanLikeTheWebUI() throws {
        let root = URL(fileURLWithPath: #filePath).deletingLastPathComponent().deletingLastPathComponent()
            .deletingLastPathComponent()
        let js = try String(contentsOf: root.appending(path: "calictl/webui/strings.de.js"), encoding: .utf8)
        // `"key": "value"` entries (also with the value on the next line); concatenated values are skipped.
        let entry = try Regex(#""((?:[^"\\]|\\.)*)"\s*:\s*"((?:[^"\\]|\\.)*)"(?=\s*[,}])"#)
        var webDE: [String: String] = [:]
        for m in js.matches(of: entry) {
            webDE[String(m.output[1].substring!)] = String(m.output[2].substring!)
        }
        #expect(webDE["Camping mode"] == "Campingmodus")  // the parse works

        var checked = 0
        for name in ["Localizable", "InfoPlist"] {
            let data = try Data(contentsOf: root.appending(path: "ios/App/\(name).xcstrings"))
            let catalog = try JSONDecoder().decode(Catalog.self, from: data)
            #expect(!catalog.strings.isEmpty)
            for (key, s) in catalog.strings {
                let en = s.localizations?["en"]?.stringUnit.value
                let de = s.localizations?["de"]?.stringUnit.value
                #expect(en?.isEmpty == false, "\(name): \(key) has no English value")
                #expect(de?.isEmpty == false, "\(name): \(key) has no German value")
                if let en, let web = webDE[en] {
                    #expect(de == web, "\(name): \(key) DE is \(de ?? "nil"), strings.de.js says \(web)")
                    checked += 1
                }
            }
        }
        #expect(checked > 0)  // at least one label is shared with the web UI
    }

    private struct Catalog: Decodable {
        struct Unit: Decodable { let value: String }
        struct Localization: Decodable { let stringUnit: Unit }
        struct Entry: Decodable { let localizations: [String: Localization]? }
        let strings: [String: Entry]
    }
}

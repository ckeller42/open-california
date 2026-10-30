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
        try await Task.sleep(for: .milliseconds(100))
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

    /// Every key of the app's string catalogs has an English and a German value. Where the web UI
    /// has the same English string, the German value is the one of `strings.de.js`.
    ///
    /// - Test: T_IOS_STRINGS_EN_DE
    /// - Verifies: R_IOS_I18N
    @Test func stringsHaveEnglishAndGermanLikeTheWebUI() throws {
        let root = URL(fileURLWithPath: #filePath).deletingLastPathComponent().deletingLastPathComponent().deletingLastPathComponent()
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

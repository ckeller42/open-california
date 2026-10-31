import CaliBLE
import CaliCore
import Foundation
import Testing
@testable import Camper

/// Each control sends exactly the `function/what/value` that the web UI sends (`calictl/webui/app.js`
/// FEATURES and `renderLighting`), and the result banner never claims more than the unit confirmed.
@MainActor
struct CommandMappingTests {
    private static let en = Locale(identifier: "en_US")

    /// The camping screen's switches: `campingmode master|lights|usb on|off` (app.js FEATURES.campingmode).
    ///
    /// - Test: T_IOS_CMD_CAMPING
    /// - Verifies: R_IOS_FUNCTIONS
    @Test func campingToggleSendsMaster() {
        #expect(CampingView.master(true) == Command(function: "campingmode", what: "master", value: "on"))
        #expect(CampingView.master(false) == Command(function: "campingmode", what: "master", value: "off"))
        #expect(CampingView.lights(true) == Command(function: "campingmode", what: "lights", value: "on"))
        #expect(CampingView.usb(false) == Command(function: "campingmode", what: "usb", value: "off"))
    }

    /// The favourite tiles are the web UI's `FAV_TILES` A/B/C/D = unit profiles 1/5/6/7 (the app's
    /// FAVORITE slots), so the third tile (C) sends `lighting profile 6`, not 3.
    ///
    /// - Test: T_IOS_CMD_FAVOURITE
    /// - Verifies: R_IOS_FUNCTIONS
    @Test func favouriteThreeSendsProfile6() {
        #expect(LightsView.favourites.map(\.label) == ["A", "B", "C", "D"])
        #expect(LightsView.favourites.map(\.profile) == [1, 5, 6, 7])
        #expect(LightsView.favourite(LightsView.favourites[2].profile) == Command(function: "lighting", what: "profile", value: "6"))
    }

    /// "All lights" is the web UI's master switch `lighting power on|off` (the app's LIGHTS_ON/OFF
    /// profiles); `lighting all` takes a brightness 0–10, never on/off. The lamps send their zone key.
    ///
    /// - Test: T_IOS_CMD_ALL_LIGHTS
    /// - Verifies: R_IOS_FUNCTIONS
    @Test func allLightsSendsPower() {
        #expect(LightsView.allLights(true) == Command(function: "lighting", what: "power", value: "on"))
        #expect(LightsView.allLights(false) == Command(function: "lighting", what: "power", value: "off"))
        #expect(LightsView.lamp("kitchen-ambient", 7) == Command(function: "lighting", what: "kitchen-ambient", value: "7"))
        #expect(LightsView.groups.flatMap(\.lamps).map(\.what) == [
            "reading-1", "reading-2", "reading-3", "kitchen-ambient", "kitchen-cabinet", "kitchen",
            "roof-ambient", "roof-reading", "outside-rear", "entrance"])
    }

    /// The cooler and the energy mode (app.js FEATURES.cooler / FEATURES.energy).
    ///
    /// - Test: T_IOS_CMD_COOLER_ENERGY
    /// - Verifies: R_IOS_FUNCTIONS
    @Test func coolerAndEnergyModeSendTheWebTokens() {
        #expect(CoolerView.power(true) == Command(function: "cooler", what: "power", value: "on"))
        #expect(CoolerView.level(3) == Command(function: "cooler", what: "level", value: "3"))
        #expect(CoolerView.modes.map(\.key) == ["normal", "quiet", "timer_quiet"])
        #expect(CoolerView.mode("quiet") == Command(function: "cooler", what: "mode", value: "quiet"))
        #expect(EnergyModeView.modes.map(\.key) == ["normal", "max_charge", "eco"])
        #expect(EnergyModeView.mode("max_charge") == Command(function: "energy", what: "mode", value: "max_charge"))
    }

    /// The air heater is controlled only on its own screen: the cockpit tile opens that screen (it
    /// never sends), and no other source file of the app builds an `airheater` command (no Siri
    /// intent, no cockpit toggle). The screen sends the web UI's tokens; continuous heating only OFF.
    ///
    /// - Test: T_IOS_CMD_HEATER_ONLY_SCREEN
    /// - Verifies: R_IOS_SIRI_NO_HEATER, R_AIRHEATER_APP_GATES
    @Test func heaterOnlyFromHeaterScreen() throws {
        for fixture in Fixture.allCases {
            let store = CamperStore.preview(fixture)
            // Open the screen, or connect again in the power-save rest; there is no command case.
            #expect([.open(.airHeater), .connect].contains(CockpitView.tap(.airHeater, store: store)), "\(fixture)")
        }
        #expect(CockpitView.tap(.airHeater, store: .preview(.campingOn)) == .open(.airHeater))

        #expect(AirHeaterView.power(true) == Command(function: "airheater", what: "power", value: "on"))
        #expect(AirHeaterView.permanentOff == Command(function: "airheater", what: "permanent", value: "off"))
        #expect(AirHeaterView.level(10) == Command(function: "airheater", what: "level", value: "10"))
        #expect(AirHeaterView.runtime(60) == Command(function: "airheater", what: "runtime", value: "60"))
        #expect(Array(AirHeaterView.runtimes) == Array(stride(from: 10, through: 120, by: 10)))

        let app = URL(fileURLWithPath: #filePath).deletingLastPathComponent().deletingLastPathComponent().appending(path: "App")
        let files = try #require(FileManager.default.enumerator(at: app, includingPropertiesForKeys: nil))
        var builders: [String] = []
        for case let url as URL in files where url.pathExtension == "swift" {
            let src = try String(contentsOf: url, encoding: .utf8)
            if src.contains("function: \"airheater\"") || src.contains("onOff(\"airheater\"") { builders.append(url.lastPathComponent) }
        }
        #expect(builders == ["AirHeaterView.swift"])
    }

    /// "Applied" only for `.applied`. A sent command says what the user can check, never "applied".
    ///
    /// - Test: T_IOS_RESULT_BANNER
    /// - Verifies: R_IOS_HONEST
    @Test func resultBannerNeverSaysAppliedForSent() {
        func text(_ r: CommandResult, _ fn: String) -> String { ResultBanner.text(r, function: fn).string(Self.en) }
        #expect(text(.applied, "lighting") == "Applied")
        #expect(text(.sent, "lighting") == "Sent — check the lamp")
        #expect(text(.sent, "cooler") == "Sent — the unit didn't confirm it")
        #expect(text(.unconfirmed, "campingmode") == "Sent — the unit didn't confirm it")
        for r in Self.allResults where r != .applied {
            for fn in ["lighting", "cooler", "campingmode", "airheater", "energy"] {
                #expect(!text(r, fn).localizedCaseInsensitiveContains("applied"), "\(r) \(fn)")
            }
        }
        #expect(ResultBanner.text(.applied, function: "cooler").string(Locale(identifier: "de_DE")) == "Übernommen")
    }

    /// Review Focus 5: a unit with CommunicationVersion 3 locks every control with the reason, and a
    /// command that still gets through to the runner reports the same text.
    ///
    /// - Test: T_IOS_V3_NOT_SUPPORTED_TEXT
    /// - Verifies: R_IOS_V2_ONLY, R_IOS_HONEST
    @Test func v3ShowsNotSupportedText() {
        let store = CamperStore.preview(.unsupportedV3)
        for fn in ["campingmode", "lighting", "cooler", "airheater", "energy"] {
            #expect(store.lockReason(fn)?.string(Self.en) == "This camper unit is not supported yet", "\(fn)")
        }
        #expect(ResultBanner.text(.notSupported, function: "cooler").string(Self.en) == "This camper unit is not supported yet")
        // The screen opens anyway (as the web UI's tiles do); it shows the reason.
        #expect(CockpitView.tap(.camping, store: store) == .open(.camping))
    }

    /// The lock reason follows the link: rest → tap to connect (the cockpit tile then connects),
    /// connecting → "Connecting…", offline → "not available while the van is offline".
    ///
    /// - Test: T_IOS_LOCK_REASON
    /// - Verifies: R_IOS_HONEST, R_IOS_POWER_SAVE
    @Test func lockReasonMatchesTheLink() {
        let store = CamperStore.preview(.parked)
        #expect(store.lockReason("campingmode") == nil)
        #expect(CockpitView.tap(.camping, store: store) == .open(.camping))
        store.link = .rest(until: Date())
        #expect(store.lockReason("campingmode")?.string(Self.en) == "Disconnected to save energy — tap to connect")
        #expect(CockpitView.tap(.camping, store: store) == .connect)
        store.link = .connecting
        #expect(store.lockReason("campingmode")?.string(Self.en) == "Connecting…")
        #expect(CockpitView.tap(.camping, store: store) == .open(.camping))
        store.link = .pairing
        #expect(store.lockReason("campingmode")?.string(Self.en) == "Not available while pairing")
        store.link = .unreachable("x")
        #expect(store.lockReason("campingmode")?.string(Self.en) == "Not available while the van is unreachable")
        store.link = .off
        #expect(store.lockReason("campingmode")?.string(Self.en) == "Not available while the van is offline")
        #expect(CockpitView.tap(.camping, store: store) == .open(.camping))
    }

    /// Every result the runner can give, with each refusal and failure case.
    static let allResults: [CommandResult] = [.applied, .sent, .unconfirmed, .notSupported, .busy, .notReady, .timeout]
        + Refusal.allCases.map { .refused($0) }
        + [Failure.resting(retryAfter: 5), .bluetoothOff, .unreachable, .linkEnded, .notTaken].map { .failed($0) }

    /// Every result text (each planner refusal, each runner failure) has an English and a German
    /// catalog entry: a German user never sees the planner's English (Ruling 30).
    ///
    /// - Test: T_IOS_REASONS_EN_DE
    /// - Verifies: R_IOS_I18N
    @Test func everyReasonHasGerman() throws {
        let root = URL(fileURLWithPath: #filePath).deletingLastPathComponent().deletingLastPathComponent()
        let json = try JSONSerialization.jsonObject(with: Data(contentsOf: root.appending(path: "App/Localizable.xcstrings"))) as? [String: Any]
        let strings = try #require(json?["strings"] as? [String: Any])
        func de(_ key: String) -> String? {
            ((((strings[key] as? [String: Any])?["localizations"] as? [String: Any])?["de"] as? [String: Any])?["stringUnit"] as? [String: Any])?["value"] as? String
        }
        var keys: Set<String> = ["Connecting in %lld s…", "Tap to connect", "Tap to connect (in %lld s)"]
        for r in Self.allResults { for fn in ["lighting", "cooler"] { keys.insert(ResultBanner.text(r, function: fn).key) } }
        keys.insert("Unknown fault of the energy system")
        for k in keys { #expect(de(k)?.isEmpty == false, "no German for \(k)") }
        #expect(ResultBanner.text(.refused(.quietNeedsFridgeOn), function: "cooler").string(Locale(identifier: "de_DE")) == "Zuerst die Kühlbox einschalten")
        #expect(ResultBanner.text(.failed(.resting(retryAfter: 5)), function: "cooler").string(Locale(identifier: "de_DE"))
            == "Die Camper-Einheit spart Energie — in 5 s erneut versuchen")
    }

    /// A command shows "Sending…" and then its result where the user looks (pinned under the pill).
    /// A tap in the power-save rest shows "Connecting in N s…" during the unit's cooldown, then dials
    /// once; a tap that the app left for the background before the cooldown ended does not dial.
    ///
    /// - Test: T_IOS_FEEDBACK
    /// - Verifies: R_IOS_HONEST, R_IOS_POWER_SAVE
    @Test func feedbackShowsSendingResultAndCooldown() async throws {
        let store = CamperStore.preview(.unsupportedV3)
        let r = await store.run(CampingView.master(true))
        #expect(r == .notSupported)
        #expect(store.feedback == .result(.notSupported, function: "campingmode"))

        func resting() -> (CamperStore, FakeCamperTransport) {
            let fake = FakeCamperTransport(frames: Dictionary(uniqueKeysWithValues: Fixture.parked.frames.compactMap { fn, b in
                CamperGATT.stateChars[fn].map { ($0, b) } }))
            let s = CamperStore(session: CamperSession(transport: fake, clock: ContinuousClock()),
                                state: Fixture.parked.state(now: Date()), link: .rest(until: Date().addingTimeInterval(0.6)))
            return (s, fake)
        }
        let (store2, fake) = resting()
        let tap = Task { await store2.tapToConnect() }
        try await Task.sleep(for: .milliseconds(100))
        guard case .connecting(let until?) = store2.feedback else { Issue.record("no countdown: \(String(describing: store2.feedback))"); return }
        #expect(FeedbackBanner.text(.connecting(until: until), now: until.addingTimeInterval(-3))?.string(Self.en) == "Connecting in 3 s…")
        #expect(await fake.connects == 0)  // the cooldown runs
        await tap.value
        #expect(await fake.connects == 1)
        #expect(store2.feedback == nil)  // connected: nothing to say

        let (store3, fake3) = resting()
        let tap3 = Task { await store3.tapToConnect() }
        try await Task.sleep(for: .milliseconds(100))
        store3.inForeground = false
        await tap3.value
        #expect(await fake3.connects == 0)
        #expect(store3.feedback == nil)
    }
}

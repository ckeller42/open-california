import CaliBLE
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
            // Open the screen, connect (rest) or nothing (locked); there is no command case.
            #expect([nil, .open(.airHeater), .connect].contains(CockpitView.tap(.airHeater, store: store)), "\(fixture)")
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
        let others: [CommandResult] = [.sent, .unconfirmed, .refused("x"), .elsewhere("x"), .notSupported, .busy,
                                       .notReady, .failed("x"), .timeout]
        for r in others {
            for fn in ["lighting", "cooler", "campingmode", "airheater", "energy"] {
                #expect(!text(r, fn).localizedCaseInsensitiveContains("applied"), "\(r) \(fn)")
                #expect(!ResultBanner.isSuccess(r))
            }
        }
        #expect(ResultBanner.isSuccess(.applied))
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
        #expect(CockpitView.tap(.camping, store: store) == nil)
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
        #expect(CockpitView.tap(.camping, store: store) == nil)
        store.link = .off
        #expect(store.lockReason("campingmode")?.string(Self.en) == "Not available while the van is offline")
    }
}

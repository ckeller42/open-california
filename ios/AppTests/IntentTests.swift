import AppIntents
import CaliBLE
import CaliCore
import Foundation
import Testing

@testable import Camper

/// The Siri and Shortcuts actions over the fake transport: the command each one sends, and what
/// Siri says back (EN + DE).
@MainActor
struct IntentTests {
    private static let en = Locale(identifier: "en_US")
    private static let de = Locale(identifier: "de_DE")
    private static let root = URL(fileURLWithPath: #filePath).deletingLastPathComponent().deletingLastPathComponent()
        .deletingLastPathComponent()

    /// The parked fixture's session (on `clock`) and its fake transport.
    private func rig(clock: any Clock<Duration> = ContinuousClock()) -> (CamperSession, FakeCamperTransport) {
        let fake = Fixture.parked.makeTransport()
        return (CamperSession(transport: fake, clock: clock), fake)
    }

    private func until(_ what: String, _ cond: () async -> Bool) async throws {
        for _ in 0..<200 {
            if await cond() { return }
            try await Task.sleep(for: .milliseconds(50))
        }
        Issue.record("timed out waiting for \(what)")
    }

    /// "Turn camping mode on" sends `campingmode master on` (the camping screen's builder). Siri says
    /// "is on" only when the unit pushes the new state.
    ///
    /// - Test: T_IOS_SIRI_CAMPING
    /// - Verifies: R_IOS_SIRI, R_IOS_SIRI_HONEST
    @Test func campingIntentSendsMasterOn() async throws {
        #expect(SetCampingModeIntent(on: true).command == Command(function: "campingmode", what: "master", value: "on"))
        #expect(SetCampingModeIntent(on: false).command == CampingView.master(false))

        let (session, fake) = rig()
        let char = try #require(CamperGATT.controlChars["campingmode"])
        let run = Task { await IntentRunner.perform(SetCampingModeIntent(on: true).command, session: session) }
        try await until("the camping frame") { await fake.writes.contains { $0.char == char } }
        let planned = CaliControl.plan(
            function: "campingmode", what: "master", value: "on", localNow: nil,
            state: Fixture.parked.state(now: Date()).planFields)
        guard case .frames(let frames) = planned else {
            Issue.record("no plan: \(planned)")
            return
        }
        #expect(await fake.writes.filter { $0.char == char }.map(\.bytes) == frames.map(\.data))
        // The unit pushes the new state: master on.
        await fake.seed(try #require(CamperGATT.stateChars["campingmode"]), [0x21])
        let outcome = await run.value
        #expect(outcome.success)
        #expect(outcome.dialog.string(Self.en) == "Camping mode is on")
        #expect(outcome.dialog.string(Self.de) == "Campingmodus ist an")
    }

    /// Favourite B is the web UI's second tile: unit profile 5 (A/B/C/D = 1/5/6/7); "1"–"4" are
    /// spoken synonyms of A–D. Lights on/off is `lighting power`, the cooler `cooler power`.
    ///
    /// - Test: T_IOS_SIRI_FAVOURITE
    /// - Verifies: R_IOS_SIRI
    @Test func favouriteBIsProfile5() {
        #expect(ActivateLightFavouriteIntent(favourite: .b).command == Command(function: "lighting", what: "profile", value: "5"))
        #expect(
            LightFavourite.allCases.map { ActivateLightFavouriteIntent(favourite: $0).command.value } == ["1", "5", "6", "7"])
        #expect(LightFavourite.allCases.map(\.rawValue) == LightsView.favourites.map(\.label))
        #expect(SetLightsIntent(on: true).command == LightsView.allLights(true))
        #expect(SetLightsIntent(on: false).command == Command(function: "lighting", what: "power", value: "off"))
        #expect(SetCoolerIntent(on: true).command == Command(function: "cooler", what: "power", value: "on"))
        #expect(SetCoolerIntent(on: false).command == CoolerView.power(false))
    }

    /// No App Shortcut runs the air heater; the app declares exactly the four known intents (the
    /// Shortcuts app shows every `AppIntent` of the target), each of them builds only camping,
    /// lighting or cooler commands for every parameter value, and the runner refuses an
    /// `airheater` command without a dial.
    ///
    /// - Test: T_IOS_SIRI_NO_HEATER
    /// - Verifies: R_IOS_SIRI_NO_HEATER
    @Test func noHeaterIntentExists() async throws {
        let shortcuts = CamperShortcuts.appShortcuts
        #expect(shortcuts.count == 4)
        for s in shortcuts {
            #expect(!String(reflecting: s).localizedCaseInsensitiveContains("heater"), "\(s)")
        }
        // Every conformer to `AppIntent` or one of its sub-protocols (`SetValueIntent`, `OpenIntent`, …),
        // also through an extension, in ios/App/**.
        let app = Self.root.appending(path: "ios/App")
        let files = try #require(FileManager.default.enumerator(at: app, includingPropertiesForKeys: nil)?.allObjects as? [URL])
        let conformer = try Regex(#"(?:struct|class|enum|extension)\s+(\w+)\s*:[^{]*\b\w*Intent\b"#)
        var intents: Set<String> = []
        for url in files where url.pathExtension == "swift" {
            let src = try String(contentsOf: url, encoding: .utf8)
            for m in src.matches(of: conformer) { intents.insert(String(m.output[1].substring!)) }
        }
        #expect(intents == ["SetCampingModeIntent", "SetLightsIntent", "ActivateLightFavouriteIntent", "SetCoolerIntent"])

        // What each one can send, for every parameter value.
        var commands = [true, false].flatMap { on in
            [SetCampingModeIntent(on: on).command, SetLightsIntent(on: on).command, SetCoolerIntent(on: on).command]
        }
        commands += LightFavourite.allCases.map { ActivateLightFavouriteIntent(favourite: $0).command }
        for c in commands { #expect(["campingmode", "lighting", "cooler"].contains(c.function), "\(c)") }

        // The choke point refuses the heater.
        let (session, fake) = rig()
        let outcome = await IntentRunner.perform(
            Command(function: "air" + "heater", what: "power", value: "on"), session: session)
        #expect(!outcome.success)
        #expect(outcome.dialog.string(Self.en) == "Not available in this app")
        #expect(await fake.connects == 0)
        #expect(await fake.writes.isEmpty)
    }

    /// Every voice command runs on a locked iPhone without Face ID; any other policy fails.
    ///
    /// - Test: T_IOS_SIRI_AUTH
    /// - Verifies: R_IOS_SIRI, AD_IOS_SIRI_LOCKED
    @Test func intentsRunOnALockedDevice() {
        let policies: [IntentAuthenticationPolicy] = [
            SetCampingModeIntent.authenticationPolicy, SetLightsIntent.authenticationPolicy,
            ActivateLightFavouriteIntent.authenticationPolicy, SetCoolerIntent.authenticationPolicy,
        ]
        let wanted = String(describing: IntentAuthenticationPolicy.alwaysAllowed)
        // The comparison can fail: the other policies describe differently.
        #expect(String(describing: IntentAuthenticationPolicy.requiresLocalDeviceAuthentication) != wanted)
        #expect(String(describing: IntentAuthenticationPolicy.requiresAuthentication) != wanted)
        for p in policies {
            #expect(String(describing: p) == wanted)
        }
    }

    /// Review Focus 2: Siri with Bluetooth off (or not allowed) names it, well within the budget.
    ///
    /// - Test: T_IOS_SIRI_BLUETOOTH_OFF
    /// - Verifies: R_IOS_SIRI_HONEST, R_IOS_HONEST
    @Test func bluetoothOffAnswersWithinBudget() async {
        for a in [BluetoothAvailability.poweredOff, .unauthorized] {
            let (session, fake) = rig()
            await fake.setBluetooth(unavailable: a)
            let start = ContinuousClock.now
            let outcome = await IntentRunner.perform(SetCoolerIntent(on: true).command, session: session)
            #expect(ContinuousClock.now - start < .seconds(9))
            #expect(!outcome.success)
            #expect(outcome.dialog.string(Self.en) == "Bluetooth is off or not allowed for this app")
            #expect(outcome.dialog.string(Self.de) == "Bluetooth ist aus oder für diese App nicht erlaubt")
        }
    }

    /// The budget ends before any frame went out (the arm delay): the app's "did not answer in time".
    ///
    /// - Test: T_IOS_SIRI_BUDGET
    /// - Verifies: R_IOS_SIRI_HONEST
    @Test func budgetExceededSaysNoAnswer() async {
        let (session, _) = rig()
        let start = ContinuousClock.now
        // The arm delay alone (3 s) is longer than this budget.
        let outcome = await IntentRunner.perform(SetCoolerIntent(on: true).command, session: session, budget: .seconds(1))
        #expect(ContinuousClock.now - start < .seconds(2))
        #expect(!outcome.success)
        #expect(outcome.dialog.string(Self.en) == "The camper unit did not answer in time")
        #expect(outcome.dialog.string(Self.de) == "Die Camper-Einheit hat nicht rechtzeitig geantwortet")
    }

    /// The budget ends after the frame went out, and the unit's push comes later: Siri says it was
    /// sent (never "no answer"), and the real result still reaches the banner.
    ///
    /// - Test: T_IOS_SIRI_BUDGET_SENT
    /// - Verifies: R_IOS_SIRI_HONEST, R_IOS_HONEST
    @Test func budgetEndsAfterTheFrameSaysSent() async throws {
        let (session, fake) = rig()
        try await session.ensureConnected(timeout: .seconds(5))
        try await until("the arm delay") { await (session.sinceFirstBeat ?? .zero) > CamperGATT.armDelay }
        var late: CommandResult?
        let outcome = await IntentRunner.perform(CampingView.master(true), session: session, budget: .milliseconds(500)) {
            late = $0
        }
        #expect(!outcome.success)
        #expect(outcome.dialog.string(Self.en) == "Camping mode: sent, but the camper unit did not confirm in time")
        #expect(
            outcome.dialog.string(Self.de) == "Campingmodus: gesendet, aber die Camper-Einheit hat es nicht rechtzeitig bestätigt"
        )
        #expect(late == nil)
        await fake.seed(try #require(CamperGATT.stateChars["campingmode"]), [0x21])
        try await until("the late result") { late != nil }
        #expect(late == .applied)
    }

    /// Right after the power-save hang-up, Siri does not dial into the unit's cooldown: it says when
    /// to try again, in words (no unit letters), and the transport sees no new dial.
    ///
    /// - Test: T_IOS_SIRI_REST
    /// - Verifies: R_IOS_SIRI_HONEST, R_IOS_POWER_SAVE
    @Test func restAnswersTryAgain() async throws {
        let (session, fake) = rig()
        try await session.ensureConnected(timeout: .seconds(5))
        await fake.hangUp(after: .zero)
        try await until("rest") { if case .rest = await session.link { true } else { false } }
        let dials = await fake.connects
        let outcome = await IntentRunner.perform(CampingView.master(true), session: session)
        #expect(!outcome.success)
        #expect(await fake.connects == dials)
        let text = outcome.dialog.string(Self.en)
        #expect(text.hasPrefix("The camper unit saves power — try again in ") && text.hasSuffix(" seconds"), "\(text)")
        #expect(outcome.dialog.string(Self.de).hasPrefix("Die Camper-Einheit spart Energie — in "))
    }

    /// Later in the cooldown (about 3.5 s left) Siri still says "try again" and does not dial: the
    /// rest of the cooldown and a connect do not fit the budget.
    ///
    /// - Test: T_IOS_SIRI_REST_LATE
    /// - Verifies: R_IOS_SIRI_HONEST, R_IOS_POWER_SAVE
    @Test func restLateInTheCooldownAnswersTryAgain() async throws {
        let clock = TestClock()  // the session's: the cooldown left is exact
        let (session, fake) = rig(clock: clock)
        try await session.ensureConnected(timeout: .seconds(5))
        await fake.hangUp(after: .zero)
        try await until("rest") { if case .rest = await session.link { true } else { false } }
        clock.advance(by: .milliseconds(3500))
        let dials = await fake.connects
        let outcome = await IntentRunner.perform(CoolerView.power(true), session: session)
        #expect(await fake.connects == dials)
        #expect(outcome.dialog.string(Self.en) == "The camper unit saves power — try again in 4 seconds")
    }

    /// Without an adopted unit, or with a bond the unit dropped, Siri says so (not "not reachable").
    ///
    /// - Test: T_IOS_SIRI_BOND_REASON
    /// - Verifies: R_IOS_SIRI_HONEST, R_IOS_I18N
    @Test func missingOrStaleBondIsSaid() async throws {
        let (session, fake) = rig()
        await fake.notPairedOnNextConnect()
        let notPaired = await IntentRunner.perform(CampingView.master(true), session: session)
        #expect(notPaired.result == .failed(.notPaired))
        #expect(notPaired.dialog.string(Self.en) == "No camper unit is paired yet — pair it under Setup")
        #expect(notPaired.dialog.string(Self.de) == "Noch keine Camper-Einheit gekoppelt — unter Einrichtung koppeln")

        let (session2, fake2) = rig()
        await fake2.requirePairingOnNextConnect()
        let stale = await IntentRunner.perform(CoolerView.power(true), session: session2)
        #expect(stale.result == .failed(.pairingRequired))
        #expect(stale.dialog.string(Self.en) == "The camper unit no longer knows this iPhone")
        #expect(stale.dialog.string(Self.de) == "Die Camper-Einheit kennt dieses iPhone nicht mehr")
    }

    /// Siri's banner never covers a UI command: while one runs, Siri's `.busy` is only spoken and the
    /// UI's "Sending…" (which locks the controls) stays. A result that comes after Siri answered
    /// shows only while the banner is still Siri's own "Sending…".
    ///
    /// - Test: T_IOS_SIRI_BANNER
    /// - Verifies: R_IOS_HONEST, R_IOS_SIRI_HONEST
    @Test func siriLeavesTheUIsBannerAlone() async throws {
        let busy = CamperStore.preview(.parked)
        busy.show(.sending(function: "lighting"), clearAfter: nil)
        await busy.session.beginCommand()
        let o = await IntentRunner.perform(CoolerView.power(true), store: busy)
        #expect(o.result == .busy)
        try await Task.sleep(for: .milliseconds(200))
        #expect(busy.feedback == .sending(function: "lighting"))
        #expect(busy.isSending)
        await busy.session.endCommand()

        // Siri answers before the arm delay ends (the session's clock stands); a newer banner comes.
        let clock = TestClock()
        let fake = Fixture.parked.makeTransport()
        let late = CamperStore(session: CamperSession(transport: fake, clock: clock), state: Fixture.parked.state(now: Date()))
        let answer = await IntentRunner.perform(CampingView.master(true), store: late, budget: .milliseconds(300))
        #expect(answer.result == nil)
        #expect(late.feedback == .sending(function: "campingmode"))
        late.show(.result(.sent, function: "lighting"), clearAfter: nil)
        clock.advance(by: CamperGATT.armDelay)
        try await until("the camping frame") { await late.session.commandIssuedFrame }
        await fake.seed(try #require(CamperGATT.stateChars["campingmode"]), [0x21])
        try await Task.sleep(for: .milliseconds(300))
        #expect(late.feedback == .result(.sent, function: "lighting"))
        // The first command has ended (its result was held back, not lost): a second is not busy.
        #expect(await IntentRunner.perform(CampingView.master(false), store: late, budget: .milliseconds(300)).result != .busy)
    }

    /// Only `.applied` says "is on/off"; a sent command says it was not confirmed, lighting says
    /// "check the lamp". Failures reuse the app's texts.
    ///
    /// - Test: T_IOS_SIRI_DIALOGS
    /// - Verifies: R_IOS_SIRI_HONEST, R_IOS_HONEST, R_IOS_I18N
    @Test func dialogsSayOnlyWhatTheUnitConfirmed() {
        func say(_ r: CommandResult, _ cmd: Command, _ l: Locale = Self.en) -> String {
            IntentRunner.outcome(r, cmd).dialog.string(l)
        }
        #expect(say(.applied, LightsView.allLights(false)) == "All lights are off")
        #expect(say(.applied, LightsView.favourite(6)) == "Favourite C is on")
        #expect(say(.applied, CoolerView.power(true), Self.de) == "Die Kühlbox ist an")
        #expect(say(.sent, CampingView.master(true)) == "Camping mode: sent, not confirmed")
        #expect(say(.unconfirmed, CoolerView.power(true), Self.de) == "Kühlbox: gesendet, nicht bestätigt")
        #expect(say(.sent, LightsView.allLights(true)) == "Lighting: sent, check the lamp")
        #expect(say(.sent, LightsView.allLights(true), Self.de) == "Beleuchtung: gesendet, prüfe die Lampe")
        #expect(say(.refused(.favouriteEmpty), LightsView.favourite(5)) == "This favourite is empty on the unit")
        #expect(say(.failed(.unreachable), CoolerView.power(true), Self.de) == "Die Camper-Einheit ist nicht erreichbar")
        #expect(
            say(.failed(.resting(retryAfter: 5)), CoolerView.power(true))
                == "The camper unit saves power — try again in 5 seconds")
        for r: CommandResult in [.sent, .unconfirmed, .busy, .notReady, .timeout, .notSupported, .failed(.linkEnded)] {
            for cmd in [CampingView.master(true), LightsView.allLights(true), CoolerView.power(false)] {
                let o = IntentRunner.outcome(r, cmd)
                #expect(!o.success)
                for l in [Self.en, Self.de] {
                    let s = o.dialog.string(l).lowercased()
                    #expect(
                        !s.contains(" is on") && !s.contains(" are on") && !s.contains(" ist an") && !s.contains(" sind an"),
                        "\(r) \(s)")
                }
            }
        }
    }

    /// Every App Shortcut phrase has an English and a German entry in `AppShortcuts.xcstrings`, and
    /// contains the app's name (Apple's rule).
    ///
    /// - Test: T_IOS_SIRI_PHRASES
    /// - Verifies: R_IOS_SIRI, R_IOS_I18N
    @Test func phrasesExistInEnAndDe() throws {
        let src = try String(contentsOf: Self.root.appending(path: "ios/App/Intents/CamperShortcuts.swift"), encoding: .utf8)
        // `"…\(.applicationName)…"` literals → the catalog keys (`${applicationName}`, `${state}`).
        let literal = try Regex(#""([^"\n]*\\\(\.applicationName\)[^"\n]*)""#)
        let parameter = try Regex(#"\\\(\\\.\$(\w+)\)"#)
        let placeholder = try Regex(#"\$\{\w+\}"#)
        let phrases = src.matches(of: literal).map { m in
            String(m.output[1].substring!)
                .replacingOccurrences(of: #"\(.applicationName)"#, with: "${applicationName}")
                .replacing(parameter) { "${\($0.output[1].substring!)}" }
        }
        #expect(phrases.count >= 8)

        let data = try Data(contentsOf: Self.root.appending(path: "ios/App/AppShortcuts.xcstrings"))
        let strings =
            try #require(try JSONSerialization.jsonObject(with: data) as? [String: Any])["strings"] as? [String: [String: Any]]
        for p in phrases {
            #expect(p.contains("${applicationName}"))
            let loc = strings?[p]?["localizations"] as? [String: [String: Any]]
            for lang in ["en", "de"] {
                let unit = loc?[lang]?["stringUnit"] as? [String: Any]
                let value = unit?["value"] as? String ?? ""
                #expect(value.contains("${applicationName}"), "\(lang): \(p)")
                #expect(
                    Set(p.matches(of: placeholder).map { String(p[$0.range]) })
                        == Set(value.matches(of: placeholder).map { String(value[$0.range]) }), "\(lang): \(p)")
            }
        }
    }
}

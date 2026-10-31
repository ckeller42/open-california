import Foundation
import Testing
@testable import CaliBLE
@testable import CaliCore

/// State frames as the unit sends them. general carries CommunicationVersion in its 9th byte (see
/// SessionTests). cooler: Installed + Mode 4 / Level 3 (control.json's `seed` state), State in bit 0.
/// lighting: the `seed` lamps (L10, L11, L13-L16 not fitted = 13); 16 bytes = ProfileNumber, Mode,
/// Timestamp(4), LightValue(2), then two lamps per byte, high nibble first (camper_codec.json).
private enum Fx {
    static let general = CamperGATT.stateChars["general"]!
    static let cooler = CamperGATT.stateChars["cooler"]!
    static let lighting = CamperGATT.stateChars["lighting"]!
    static let coolerCtl = CamperGATT.controlChars["cooler"]!
    static let lightingCtl = CamperGATT.controlChars["lighting"]!
    static let coolerOff: [UInt8] = [0x08, 0x43, 0, 0, 0, 0, 0, 0]
    static let coolerOn: [UInt8] = [0x09, 0x43, 0, 0, 0, 0, 0, 0]
    static let lightsSeed: [UInt8] = [0, 0, 0, 0, 0, 0, 0, 0, 0x00, 0x00, 0x00, 0x00, 0xd0, 0x0d, 0xdd, 0xdd]
    /// The fitted lamps at 5, with the given ProfileNumber and Mode.
    static func lightsAt5(pn: UInt8, mode: UInt8) -> [UInt8] {
        [pn, mode, 0, 0, 0, 0, 0, 0, 0x55, 0x55, 0x55, 0x55, 0xd5, 0x5d, 0xdd, 0xdd]
    }
    /// The unit's wake-up config frame: Mode 20, a Timestamp, LightValue 0x0101 (enabled, area 1).
    static let wakeup: [UInt8] = [0, 20, 0x6a, 0xc5, 0xed, 0xf0, 0x01, 0x01, 0xee, 0xee, 0xee, 0xee, 0xee, 0xee, 0xee, 0xee]
    static func frames(version: UInt8 = 2) -> [UInt16: [UInt8]] {
        [general: [0, 0, 0, 0, 0, 0, 0, 0, version], cooler: coolerOff, lighting: lightsSeed]
    }
    static let coolerOnCmd = Command(function: "cooler", what: "power", value: "on")
    static let lightsAll5 = Command(function: "lighting", what: "all", value: "5")
}

/// Polls `cond` in real time (the session works on its own tasks) for up to 2 s.
private func until(_ what: String, _ cond: () async -> Bool) async throws {
    for _ in 0..<2000 {
        if await cond() { return }
        try await Task.sleep(for: .milliseconds(1))
    }
    Issue.record("timed out waiting for \(what)")
}

private actor ResultBox {
    var value: CommandResult?
    func set(_ v: CommandResult) { value = v }
}

private struct Rig {
    let clock = TestClock()
    let fake: FakeCamperTransport
    let s: CamperSession

    init(_ frames: [UInt16: [UInt8]] = Fx.frames()) {
        fake = FakeCamperTransport(clock: clock, frames: frames)
        s = CamperSession(transport: fake, clock: clock)
    }

    /// Writes other than the heartbeat.
    func commandWrites() async -> [FakeCamperTransport.Write] { await fake.writes.filter { $0.char != CamperGATT.heartbeat } }
    func heartbeats() async -> [[UInt8]] { await fake.writes.filter { $0.char == CamperGATT.heartbeat }.map(\.bytes) }

    /// A link that has beaten for the arm delay already.
    func warm() async throws {
        try await s.ensureConnected(timeout: .seconds(10))
        try await until("first beat") { await s.sinceFirstBeat != nil }
        clock.advance(by: CamperGATT.armDelay)
        // The beat this advance released is written: no heartbeat write is pending any more.
        try await until("second beat") { await heartbeats().count == 2 }
    }

    func start(_ cmd: Command) -> Task<CommandResult, Never> { Task { await s.run(cmd) } }

    /// Moves the clock in 100 ms steps until the command has its result (at most 60 s of clock).
    /// It never stops at a fixed time: a sleep the runner registers late would otherwise never end.
    func finish(_ t: Task<CommandResult, Never>) async throws -> CommandResult {
        let box = ResultBox()
        Task { await box.set(await t.value) }
        for _ in 0..<600 {
            if let v = await box.value { return v }
            clock.advance(by: .milliseconds(100))
            try await Task.sleep(for: .milliseconds(1))
        }
        Issue.record("the command did not finish")
        return await t.value
    }
}

struct CommandRunnerTests {
    /// The cooler vector frame goes to 1101; lighting's commit waits 300 ms after the SET's ACK.
    /// - Test: T_IOS_CMD_FRAMES_IN_ORDER
    /// - Verifies: R_IOS_TESTED, R_IOS_FUNCTIONS
    @Test func framesWrittenInOrderWithDelays() async throws {
        let r = Rig()
        try await r.warm()
        let cooler = r.start(Fx.coolerOnCmd)
        try await until("cooler frame") { await r.commandWrites().count == 1 }
        #expect(await r.commandWrites() == [.init(char: Fx.coolerCtl, bytes: [0xfd, 0x77, 0x1e, 0x3e, 0x1f, 0x1f])])
        _ = try await r.finish(cooler)

        let l = Rig()
        try await l.warm()  // the clock stands at 3.0 s
        let lights = l.start(Command(function: "lighting", what: "power", value: "on"))
        try await until("SET frame") { await l.commandWrites().count == 1 }
        try await until("the 300 ms delay") { l.clock.deadlines.contains(.milliseconds(3300)) }
        l.clock.advance(by: .milliseconds(299))
        #expect(l.clock.deadlines.contains(.milliseconds(3300)))  // the delay did not end
        #expect(await l.commandWrites().count == 1)
        l.clock.advance(by: .milliseconds(1))
        try await until("commit frame") { await l.commandWrites().count == 2 }
        let w = await l.commandWrites()
        #expect(w[0].char == Fx.lightingCtl && w[0].bytes.first == 0x0c)  // SET_PROFILE
        #expect(w[1].char == Fx.lightingCtl && w[1].bytes.first == 0x0e)  // commit
        #expect(try await l.finish(lights) == .sent)
    }

    /// A link that came up for the command: the first frame waits 3.0 s after the first beat.
    /// - Test: T_IOS_CMD_COLD_ARM_DELAY
    /// - Verifies: R_IOS_TESTED, R_IOS_BLE_DIRECT
    @Test func coldCommandWaitsArmDelay() async throws {
        let r = Rig()
        let t = r.start(Fx.coolerOnCmd)
        try await until("the arm wait") { r.clock.deadlines.contains(CamperGATT.armDelay) }
        #expect(await r.heartbeats().first == [0x00, 0x10, 0x00, 0x00])
        r.clock.advance(by: .milliseconds(2900))
        #expect(r.clock.deadlines.contains(CamperGATT.armDelay))  // still waiting at 2.9 s
        #expect(await r.commandWrites().isEmpty)
        r.clock.advance(by: .milliseconds(100))
        try await until("frame at 3.0 s") { await r.commandWrites().count == 1 }
        #expect(try await r.finish(t) == .sent)
    }

    /// An already-beating link writes at once; a pushed matching state confirms it.
    /// - Test: T_IOS_CMD_WARM_AT_ONCE
    /// - Verifies: R_IOS_TESTED, R_IOS_HONEST
    @Test func warmCommandWritesAtOnce() async throws {
        let r = Rig()
        try await r.warm()
        let t = r.start(Fx.coolerOnCmd)
        try await until("frame without moving the clock") { await r.commandWrites().count == 1 }
        await r.fake.pushForeign(char: Fx.cooler, bytes: Fx.coolerOn)
        #expect(await t.value == .applied)
    }

    /// Only the unit's lamp-state push confirms lighting; a favourite-save ack (Mode 4 / PN 1) does not.
    /// - Test: T_IOS_CMD_LIGHT_MODE4
    /// - Verifies: R_IOS_TESTED, R_IOS_HONEST
    @Test func lightingAppliedOnlyOnMode4Push() async throws {
        let r = Rig()
        try await r.warm()
        let quiet = r.start(Fx.lightsAll5)
        try await until("SET frame") { await r.commandWrites().count == 1 }
        await r.fake.pushForeign(char: Fx.lighting, bytes: Fx.lightsAt5(pn: 1, mode: 4))
        #expect(try await r.finish(quiet) == .sent)

        let t = r.start(Fx.lightsAll5)
        try await until("SET frame") { await r.commandWrites().count == 3 }
        // The unit's lamp frame arrives before the commit (CAPTURE 2026-10-10); it is kept until the commit is ACKed.
        await r.fake.pushForeign(char: Fx.lighting, bytes: Fx.lightsAt5(pn: 9, mode: 4))
        try await until("commit delay sleeper") { r.clock.sleeperCount == 2 }
        r.clock.advance(by: .milliseconds(300))
        #expect(await t.value == .applied)
    }

    /// The write-through echo on the state char, read back, is not proof.
    /// - Test: T_IOS_CMD_READBACK_NOT_APPLIED
    /// - Verifies: R_IOS_TESTED, R_IOS_HONEST
    @Test func stateReadBackIsNotApplied() async throws {
        let r = Rig()
        try await r.warm()
        let t = r.start(Fx.lightsAll5)
        try await until("SET frame") { await r.commandWrites().count == 1 }
        await r.fake.echo(Fx.lighting, Fx.lightsAt5(pn: 9, mode: 4))
        try await r.s.readAll()
        #expect(await r.s.state.fields["lighting"]?["BrightnessLOne"] == 5)  // the read saw the echo
        #expect(try await r.finish(t) == .sent)
    }

    /// The unit hangs up after the SET went out: unconfirmed, the commit is never written.
    /// - Test: T_IOS_CMD_HANGUP_AFTER_FRAME
    /// - Verifies: R_IOS_TESTED, R_IOS_HONEST, R_IOS_POWER_SAVE
    @Test func hangUpAfterFrameIsUnconfirmed() async throws {
        let r = Rig()
        try await r.warm()
        let t = r.start(Fx.lightsAll5)
        try await until("SET frame") { await r.commandWrites().count == 1 }
        await r.fake.hangUp(after: .zero)
        try await until("link down before the commit's delay ends") { await r.s.isReady == false }
        r.clock.advance(by: .milliseconds(300))
        #expect(await t.value == .unconfirmed)
        #expect(await r.commandWrites().count == 1)
        #expect(await r.s.commands == 0)
    }

    /// The unit hangs up during the arm wait: nothing went out, so the command failed.
    /// - Test: T_IOS_CMD_HANGUP_BEFORE_FRAME
    /// - Verifies: R_IOS_TESTED, R_IOS_HONEST, R_IOS_POWER_SAVE
    @Test func hangUpBeforeFrameIsFailed() async throws {
        let r = Rig()
        let t = r.start(Fx.coolerOnCmd)
        try await until("the arm wait") { r.clock.deadlines.contains(CamperGATT.armDelay) }
        await r.fake.hangUp(after: .zero)
        try await until("link lost") { await r.s.isReady == false }
        r.clock.advance(by: CamperGATT.armDelay)
        #expect(await t.value == .failed(.linkEnded))
        #expect(await r.commandWrites().isEmpty)
        #expect(await r.s.commands == 0)
    }

    /// - Test: T_IOS_CMD_NO_ROOF
    /// - Verifies: R_IOS_TESTED, R_IOS_NO_ROOF
    @Test func roofIsRefused() async throws {
        let r = Rig()
        #expect(await r.s.run(Command(function: "roof", what: "open", value: nil)) == .refused(.noRoof))
        #expect(await r.fake.writes.isEmpty)  // not even connected
    }

    /// - Test: T_IOS_CMD_V3_NOT_SUPPORTED
    /// - Verifies: R_IOS_TESTED, R_IOS_V2_ONLY
    @Test func v3UnitIsNotSupported() async throws {
        let r = Rig(Fx.frames(version: 3))
        #expect(await r.s.run(Fx.coolerOnCmd) == .notSupported)
        #expect(await r.commandWrites().isEmpty)
    }

    /// Only the satellite's allow-list is written: the roof's control char (1401), the V3 chars and a
    /// wrong frame length are refused before any write, even if a plan held them.
    /// - Test: T_IOS_CMD_FORBIDDEN_CHAR
    /// - Verifies: R_IOS_TESTED, R_IOS_V2_ONLY, R_IOS_NO_ROOF
    @Test func forbiddenCharIsNeverWritten() async throws {
        let r = Rig()
        try await r.warm()
        #expect(CaliControl.writeOK(Fx.coolerCtl, 6))
        #expect(!CaliControl.writeOK(Fx.coolerCtl, 5))
        for char in CamperGATT.forbiddenWrites.union([0x1401]) {
            let frames = [ControlFrame(char: Fx.coolerCtl, delayMs: 0, data: [0xff]), ControlFrame(char: char, delayMs: 0, data: [0])]
            #expect(await r.s.write(frames, until: { .seconds(4) }) == .refused(.forbiddenWrite), "wrote \(char)")
        }
        #expect(await r.commandWrites().isEmpty)
    }

    /// A wake-up edit without the unit's config pulls it (REQUEST_CONFIG + commit); no reply refuses.
    /// - Test: T_IOS_CMD_LIGHT_PULL
    /// - Verifies: R_IOS_TESTED, R_IOS_FUNCTIONS
    @Test func needsLightConfigPullsThenRefusesWithoutReply() async throws {
        let r = Rig()
        try await r.warm()
        let cmd = Command(function: "lighting", what: "wakeup", value: "07:00")
        #expect(CaliControl.plan(function: "lighting", what: "wakeup", value: "07:00", localNow: 0,
                                 state: ["lighting": try CaliCodec.decode(function: "lighting", bytes: Fx.lightsSeed)]) == .needsLightConfig)
        let t = r.start(cmd)
        try await until("pull frames") { await r.commandWrites().count == 1 }
        r.clock.advance(by: .milliseconds(300))
        try await until("pull commit") { await r.commandWrites().count == 2 }
        #expect(await r.commandWrites().map(\.bytes) == CaliControl.pullPlan().map(\.data))
        #expect(try await r.finish(t) == .refused(.wakeupUnknown))

        // The unit answers the next pull with its wake-up frame (Mode 20): the edit goes out.
        let again = r.start(cmd)
        try await until("pull again") { await r.commandWrites().count == 3 }
        r.clock.advance(by: .milliseconds(300))
        try await until("pull commit") { await r.commandWrites().count == 4 }
        await r.fake.pushForeign(char: Fx.lighting, bytes: Fx.wakeup)
        try await until("wake-up edit") { await r.commandWrites().count == 5 }
        #expect(await r.commandWrites()[4].bytes[1] == 0x14)  // Mode 20
        #expect(try await r.finish(again) == .sent)  // a config echo proves nothing
        #expect(await r.s.pushSubscriberCount == 0)  // the pull's stream and the command's
    }

    /// The unit's own Mode-20 frame latches the wake-up config: the next edit plans without a pull.
    /// A new link clears the latch (control_run.c plans over this link's latch only).
    /// - Test: T_IOS_CMD_LIGHT_CFG_LATCH
    /// - Verifies: R_IOS_TESTED, R_IOS_FUNCTIONS, R_IOS_HONEST
    @Test func lightConfigLatchAvoidsThePull() async throws {
        let r = Rig()
        try await r.warm()
        await r.fake.pushForeign(char: Fx.lighting, bytes: Fx.wakeup)
        try await until("latched") { await r.s.state.lightConfig["WakeupTimestamp"] != nil }
        // A later lamp frame replaces the stored frame; the latch stays.
        await r.fake.pushForeign(char: Fx.lighting, bytes: Fx.lightsAt5(pn: 9, mode: 4))
        try await until("lamp frame") { await r.s.state.fields["lighting"]?["Mode"] == 4 }
        let st = await r.s.state
        #expect(st.lightConfig["WakeupLightValue"] == 0x0101)
        #expect(CaliControl.plan(function: "lighting", what: "wakeup", value: "07:00", localNow: 0, state: st.planFields) != .needsLightConfig)
        #expect(CaliControl.plan(function: "lighting", what: "wakeup", value: "07:00", localNow: 0, state: st.fields) == .needsLightConfig)

        await r.fake.dropLink()
        try await until("link down") { await r.s.isReady == false }
        r.clock.advance(by: .seconds(7))
        try await r.s.ensureConnected(timeout: .seconds(10))
        #expect(await r.s.state.lightConfig.isEmpty)  // the read returned a lamp frame, no config
    }

    /// Two commands at once: one runs, the other is busy at once; their frames never interleave.
    /// - Test: T_IOS_CMD_BUSY
    /// - Verifies: R_IOS_TESTED, R_IOS_HONEST
    @Test func secondCommandIsBusyAndFramesNeverInterleave() async throws {
        let r = Rig()
        try await r.warm()
        let a = r.start(Command(function: "lighting", what: "power", value: "on"))
        let b = r.start(Fx.coolerOnCmd)
        let results = [try await r.finish(a), try await r.finish(b)]
        #expect(results.filter { $0 == .busy }.count == 1)
        #expect(results.contains(.sent))
        let chars = Set(await r.commandWrites().map(\.char))
        #expect(chars.count == 1)  // only the command that ran wrote
        #expect(await r.commandWrites().count == (chars == [Fx.lightingCtl] ? 2 : 1))
        #expect(await r.s.commands == 0)
        #expect(try await r.finish(r.start(Fx.coolerOnCmd)) == .sent)  // free again
    }

    /// The push stream of a command ends with it, whatever the result.
    /// - Test: T_IOS_CMD_NO_SUBSCRIBER_LEAK
    /// - Verifies: R_IOS_TESTED
    @Test func noPushSubscriberLeftOnAnyPath() async throws {
        let r = Rig()
        try await r.warm()
        let applied = r.start(Fx.coolerOnCmd)
        try await until("frame") { await r.commandWrites().count == 1 }
        await r.fake.pushForeign(char: Fx.cooler, bytes: Fx.coolerOn)
        #expect(await applied.value == .applied)
        #expect(await r.s.pushSubscriberCount == 0)

        await r.fake.rejectNextWrite()
        #expect(await r.s.run(Fx.coolerOnCmd) == .failed(.notTaken))
        #expect(await r.s.pushSubscriberCount == 0)

        await r.fake.dropLinkDuringNextWrite(to: Fx.coolerCtl)
        #expect(await r.s.run(Fx.coolerOnCmd) == .unconfirmed)
        #expect(await r.s.pushSubscriberCount == 0)
    }

    /// A write issued and then lost with the link (no ACK) may have landed: unconfirmed, not failed.
    /// - Test: T_IOS_CMD_LINK_LOST_IN_FLIGHT
    /// - Verifies: R_IOS_TESTED, R_IOS_HONEST
    @Test func linkLostDuringWriteIsUnconfirmed() async throws {
        let r = Rig()
        try await r.warm()
        await r.fake.dropLinkDuringNextWrite(to: Fx.coolerCtl)
        #expect(await r.s.run(Fx.coolerOnCmd) == .unconfirmed)
        #expect(await r.commandWrites() == [.init(char: Fx.coolerCtl, bytes: [0xfd, 0x77, 0x1e, 0x3e, 0x1f, 0x1f])])
    }

    /// The one cooler frame is ACKed, then the unit hangs up before any push: unconfirmed.
    /// - Test: T_IOS_CMD_HANGUP_BEFORE_PUSH
    /// - Verifies: R_IOS_TESTED, R_IOS_HONEST, R_IOS_POWER_SAVE
    @Test func hangUpAfterAckBeforePushIsUnconfirmed() async throws {
        let r = Rig()
        try await r.warm()
        let t = r.start(Fx.coolerOnCmd)
        try await until("frame") { await r.commandWrites().count == 1 }
        await r.fake.hangUp(after: .zero)
        #expect(await t.value == .unconfirmed)
        #expect(await r.s.pushSubscriberCount == 0)
    }

    /// Frames of an earlier link do not count: the gate needs this link's `general`, the command
    /// this link's frame of its function (control_run.c `cali_session_frame_live`).
    /// - Test: T_IOS_CMD_THIS_LINK_ONLY
    /// - Verifies: R_IOS_TESTED, R_IOS_HONEST, R_IOS_V2_ONLY
    @Test func onlyFramesOfThisLinkCount() async throws {
        let r = Rig()
        try await r.s.ensureConnected(timeout: .seconds(10))
        // The next link offers no cooler frame.
        await r.fake.remove(Fx.cooler)
        try await reconnect(r)
        #expect(await r.s.state.fields["cooler"] != nil)  // kept from the earlier link
        #expect(try await r.finish(r.start(Fx.coolerOnCmd)) == .notReady)

        // The next link offers no general frame: the old V2 gate does not count.
        await r.fake.remove(Fx.general)
        try await reconnect(r)
        #expect(await r.s.state.gate == .supported)  // the stored frame says V2 …
        #expect(try await r.finish(r.start(Fx.coolerOnCmd)) == .notSupported)  // … but not on this link
        #expect(await r.commandWrites().isEmpty)
    }

    private func reconnect(_ r: Rig) async throws {
        await r.fake.dropLink()
        try await until("link down") { await r.s.isReady == false }
        r.clock.advance(by: .seconds(7))
        try await r.s.ensureConnected(timeout: .seconds(10))
    }
}

import CaliCore
import Foundation

/// What became of a command.
///
/// - Requirement: R_IOS_HONEST
public enum CommandResult: Sendable, Equatable {
    /// The unit's truthful feedback confirmed it (a pushed state frame, never a read-back).
    case applied
    /// Every frame was ACKed, but no confirmation arrived before the deadline.
    case sent
    /// Not sent: a gate of the planner or of the runner (`Refusal.elsewhere`: not a command of this app).
    case refused(Refusal)
    /// The unit does not speak CommunicationVersion 2, or this link has no `general` frame yet.
    case notSupported
    /// Another command is still running (control_run.c `CALI_CTL_BUSY`); nothing was written.
    case busy
    /// This link has no frame of the command's function yet (control_run.c `CALI_CTL_NOT_READY`).
    case notReady
    /// Nothing reached the unit.
    case failed(Failure)
    /// The deadline passed before every frame was ACKed: time ran out between frames, or a write hung
    /// (that frame may have reached the unit; "did not answer in time" says no more).
    case timeout
    /// A frame went out and the unit may have acted on it, but no answer can come: the link dropped
    /// after a frame was issued, or a later frame was rejected after an earlier one was ACKed.
    case unconfirmed
}

/// Why nothing reached the unit.
///
/// - Requirement: R_IOS_HONEST, R_IOS_I18N, R_IOS_POWER_SAVE
public enum Failure: Sendable, Equatable {
    /// The unit's reconnect cooldown after the power-save hang-up: try again in this many seconds.
    case resting(retryAfter: Int)
    case bluetoothOff
    case unreachable
    /// No unit is adopted yet: the pairing guide comes first.
    case notPaired
    /// The unit no longer knows this iPhone (a stale bond): Forget This Device, then pair again.
    case pairingRequired
    /// The unit ended the link before a frame went out.
    case linkEnded
    /// The unit did not take the frame (the write was refused or not issued).
    case notTaken

    /// The failure of an `ensureConnected` error.
    public init(_ error: Error) {
        switch error {
        case SessionError.inRest(let after): self = .resting(retryAfter: Int(after.components.seconds) + 1)
        case SessionError.bluetooth: self = .bluetoothOff
        case SessionError.notPaired: self = .notPaired
        case SessionError.pairingRequired: self = .pairingRequired
        default: self = .unreachable
        }
    }
}

/// One control command in calictl's grammar: `set <function> <what> <value>`.
public struct Command: Sendable, Hashable {
    public let function: String
    public let what: String
    public let value: String?

    public init(function: String, what: String, value: String?) {
        self.function = function
        self.what = what
        self.value = value
    }
}

extension CamperSession {
    /// Runs one command: connect when needed, plan it over this link's state, write its frames in
    /// order, and report what the unit confirmed (`S_SEQ_ACTUATE`, `control_run.c`).
    ///
    /// One command at a time: while one runs (from its first await to its result), another returns
    /// `.busy` at once. The `1003` heartbeat is the session's. A link that is younger than
    /// `CamperGATT.armDelay` since its first beat (it came up for this command) waits until then
    /// before the first frame; an older one writes at once. Each frame waits its `delayMs` after the
    /// previous write's ACK. The deadline `CaliControl.deadline` counts from the first frame.
    ///
    /// - Parameter connectTimeout: the budget of `ensureConnected` when the link is not ready.
    /// - Requirement: R_IOS_HONEST, R_IOS_NO_ROOF, R_IOS_V2_ONLY, R_IOS_FUNCTIONS, R_IOS_POWER_SAVE
    public func run(_ cmd: Command, connectTimeout: Duration = .seconds(10)) async -> CommandResult {
        if cmd.function == "roof" { return .refused(.noRoof) }
        // Single flight (control_run.c CALI_CTL_BUSY). It also keeps a hang-up during the command
        // from starting the power-save rest (session.c `!cali_ctl_busy()`).
        guard commands == 0 else { return .busy }
        beginCommand()
        commandIssuedFrame = false
        defer { endCommand() }
        do {
            try await ensureConnected(timeout: connectTimeout)
        } catch {
            return .failed(Failure(error))
        }
        // Only frames of this link count (control_run.c cali_session_frame_live): the gate needs this
        // link's `general`, the command its own function's frame.
        guard liveFunctions.contains("general"), state.gate == .supported else { return .notSupported }
        guard liveFunctions.contains(cmd.function) else { return .notReady }

        let since = sinceFirstBeat ?? .zero
        if since < CamperGATT.armDelay { try? await clock.sleep(for: CamperGATT.armDelay - since) }
        guard isReady else { return .failed(.linkEnded) }

        // The phone's wall clock read as UTC, taken at each plan (control_run.c adds the time waited).
        let plan: @Sendable (CamperState) -> PlanResult = { st in
            let localNow = Int64(Date().timeIntervalSince1970) + Int64(TimeZone.current.secondsFromGMT())
            return CaliControl.plan(
                function: cmd.function, what: cmd.what, value: cmd.value, localNow: localNow, state: st.planFields)
        }
        var planned = plan(state)
        if planned == .needsLightConfig {
            // Pull the unit's lighting config, then plan once more over its reply (serve._pull_lighting_config).
            if let r = await pullLightConfig(plan) { return r }
            planned = plan(state)  // still `needsLightConfig`: refused below (wakeupUnknown)
        }

        switch planned {
        case .frames(let frames):
            // Only pushes from the first frame on count: an earlier one cannot be this command's effect.
            let (id, pushes) = pushes()
            defer { unsubscribePushes(id) }
            let left = clock.countdown(CaliControl.deadline)
            if let r = await write(frames, until: left, issuing: true) { return r }
            return await confirm(cmd, pushes: pushes, within: left())
        case .refused(let why): return .refused(Refusal(planner: why))
        case .elsewhere(let why): return .refused(Refusal(planner: why))
        case .badValue: return .refused(.badValue)
        case .needsLightConfig: return .refused(.wakeupUnknown)
        }
    }

    /// Writes the lighting-config pull and waits until a lighting frame lets `plan` decide. A result
    /// ends the command; nil means: plan again over the new state.
    private func pullLightConfig(_ plan: @escaping @Sendable (CamperState) -> PlanResult) async -> CommandResult? {
        let (id, pushes) = pushes()
        defer { unsubscribePushes(id) }
        if let r = await write(CaliControl.pullPlan(), until: clock.countdown(CaliControl.deadline)) {
            // No command frame went out (control_run.c: `sent = !O.pull && …`).
            return r == .unconfirmed ? .failed(.linkEnded) : r
        }
        try? await race(clock, CaliControl.configPull) {
            for await p in pushes {
                switch p {
                case .linkDown: throw SessionError.unreachable
                case .frame(function: "lighting", state: let st) where plan(st) != .needsLightConfig: return
                case .frame: continue
                }
            }
        }
        return isReady ? nil : .failed(.linkEnded)
    }

    /// Writes `frames` in order within the time `left` gives; nil when every frame was ACKed, else
    /// the result. A frame outside the write allow-list refuses the whole command before any write.
    /// - Parameter issuing: the frames are the command's own (not the config pull): mark
    ///   `commandIssuedFrame` as each one goes out.
    func write(_ frames: [ControlFrame], until left: @Sendable () -> Duration, issuing: Bool = false) async -> CommandResult? {
        // The satellite's allow-list (cali_ctl_write_ok); the V3 deny-list stays as an extra check.
        if frames.contains(where: { !CaliControl.writeOK($0.char, $0.data.count) || CamperGATT.forbiddenWrites.contains($0.char) }
        ) {
            return .refused(.forbiddenWrite)
        }
        var acked = false
        for f in frames {
            if f.delayMs > 0 { try? await clock.sleep(for: .milliseconds(Int(f.delayMs))) }
            let budget = left()
            if budget <= .zero { return .timeout }
            if issuing { commandIssuedFrame = true }
            do {
                try await race(clock, budget) { [transport] in try await transport.write(f.char, f.data) }
                acked = true
            } catch SessionError.unreachable {
                return .timeout  // the write hung past the deadline
            } catch TransportError.linkLost {
                return .unconfirmed  // issued, then the link went: it may have landed (control_run.c:176-181)
            } catch {
                // Not issued (notConnected) or refused by the unit (writeRejected): nothing more goes out.
                // After an ACKed frame the unit may have acted on it, so that is unconfirmed; C says
                // FAILED for a refusal at any frame (control_run.c:226). This stays the more honest result.
                return acked ? .unconfirmed : .failed(.notTaken)
            }
        }
        return nil
    }

    /// Waits for a push that shows the command took; a read-back never counts.
    private func confirm(_ cmd: Command, pushes: AsyncStream<Push>, within: Duration) async -> CommandResult {
        do {
            try await race(clock, within) {
                for await p in pushes {
                    switch p {
                    case .linkDown: throw TransportError.notConnected
                    case .frame(function: let fn, state: let st) where fn == cmd.function && Self.confirms(cmd, st): return
                    case .frame: continue
                    }
                }
                throw TransportError.notConnected  // the stream ended
            }
            return .applied
        } catch SessionError.unreachable {
            return .sent
        } catch {
            return .unconfirmed
        }
    }

    /// Does this pushed state show the command's target? A port of calictl `postcheck.set_check` for
    /// the functions this app writes. Lighting counts only on a lamp-state frame (Mode 4 outside
    /// favourite 1-7, Mode 16 outside door contact: `cali_light_reports_state`). A command without
    /// a check here (a single zone, wake-up, favourite save, energy) is never `applied`, only `sent`.
    static func confirms(_ cmd: Command, _ st: CamperState) -> Bool {
        let f = st.fields[cmd.function] ?? [:]
        let i = st.interpreted[cmd.function] ?? [:]
        let v = cmd.value?.lowercased() ?? ""
        let on = ["on", "true", "1"].contains(v)
        let n = Double(v)
        switch (cmd.function, cmd.what) {
        case ("cooler", "power"): return f["State"] == (on ? 1 : 0)
        case ("cooler", "level"): return n != nil && f["Level"].map(Double.init) == n
        case ("campingmode", "master"): return i["master_on"] == .bool(on)
        case ("campingmode", "usb"): return i["usb_charger"] == .bool(on)
        case ("campingmode", "lights"): return i["lights_on"] == .bool(on)
        case ("airheater", "power"): return i["running"] == .bool(on)
        case ("airheater", "level"): return n.map { i["level"] == .number($0) } ?? false
        case ("airheater", "timer_start"): return i["timer_armed"] == .bool(true)
        case ("airheater", "timer_cancel"): return i["timer_armed"] == .bool(false)
        case ("lighting", "door_contact"): return i["door_contact"] == .bool(on)
        case ("lighting", let what):
            guard CaliControl.lightReportsState(f) else { return false }
            switch what {
            case "power": return i["profile"] == .number(on ? 12 : 0)  // CALI_LIGHT_PROFILE_ALL_ON / _OFF
            case "profile": return n.map { i["profile"] == .number($0) } ?? false
            case "all":
                // The peak of the lamps; 13 (not fitted) and 14 (unchanged) are no level.
                let peak = f.filter { $0.key.hasPrefix("Brightness") && $0.value != 13 && $0.value != 14 }.values.max() ?? 0
                // DEFAULT (11): the unit reports the lamp's own level, past the transient 1.
                return n == 11 ? (2...10).contains(peak) : n == Double(peak)
            default: return false
            }
        default: return false
        }
    }
}

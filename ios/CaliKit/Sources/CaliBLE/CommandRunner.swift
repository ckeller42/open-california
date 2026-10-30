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
    case refused(String)
    case elsewhere(String)
    /// The unit does not speak CommunicationVersion 2 (or has not said which one yet).
    case notSupported
    /// Nothing reached the unit.
    case failed(String)
    /// The frames did not go out before the deadline.
    case timeout
    /// The link dropped after a frame went out: the unit may have acted on it.
    case unconfirmed
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
    /// The `1003` heartbeat is the session's. A link that is younger than `CamperGATT.armDelay`
    /// since its first beat (it came up for this command) waits until then before the first frame;
    /// an older one writes at once. Each frame waits its `delayMs` after the previous write's ACK.
    /// The deadline `CaliControl.deadline` counts from the first frame.
    ///
    /// - Parameter connectTimeout: the budget of `ensureConnected` when the link is not ready.
    /// - Requirement: R_IOS_HONEST, R_IOS_NO_ROOF, R_IOS_V2_ONLY, R_IOS_FUNCTIONS, R_IOS_POWER_SAVE
    public func run(_ cmd: Command, connectTimeout: Duration = .seconds(10)) async -> CommandResult {
        if cmd.function == "roof" { return .refused("This app does not move the roof.") }
        do {
            try await ensureConnected(timeout: connectTimeout)
        } catch SessionError.inRest(let after) {
            return .failed("The camper unit saves power. Try again in \(after.components.seconds + 1) s.")
        } catch SessionError.bluetooth {
            return .failed("Bluetooth is not available.")
        } catch {
            return .failed("The camper unit is not reachable.")
        }
        guard state.gate == .supported else { return .notSupported }

        // A hang-up during the command reconnects instead of resting (session.c `!cali_ctl_busy()`).
        beginCommand()
        defer { endCommand() }
        let since = sinceFirstBeat ?? .zero
        if since < CamperGATT.armDelay { try? await clock.sleep(for: CamperGATT.armDelay - since) }
        guard isReady else { return .failed("The camper unit ended the link.") }

        let localNow = Int64(Date().timeIntervalSince1970) + Int64(TimeZone.current.secondsFromGMT())
        var plan = CaliControl.plan(function: cmd.function, what: cmd.what, value: cmd.value, localNow: localNow, state: state.planFields)
        if plan == .needsLightConfig {
            // Pull the unit's lighting config, then plan once more over its reply (serve._pull_lighting_config).
            let pull = CaliControl.pullPlan()
            let pushes = pushes()
            if let r = await write(pull, until: clock.countdown(CaliControl.deadline)) {
                return r == .unconfirmed ? .failed("The camper unit ended the link.") : r
            }
            let replan: @Sendable (CamperState) -> PlanResult = { st in
                CaliControl.plan(function: cmd.function, what: cmd.what, value: cmd.value, localNow: localNow, state: st.planFields)
            }
            try? await race(clock, CaliControl.configPull) {
                for await p in pushes {
                    switch p {
                    case .linkDown: throw SessionError.unreachable
                    case .frame(function: "lighting", state: let st) where replan(st) != .needsLightConfig: return
                    case .frame: continue
                    }
                }
            }
            guard isReady else { return .failed("The camper unit ended the link.") }
            plan = replan(state)
            if plan == .needsLightConfig { return .refused(CaliControl.wakeupUnknown) }
        }

        switch plan {
        case .frames(let frames):
            // Only pushes from the first frame on count: an earlier one cannot be this command's effect.
            let pushes = pushes()
            let left = clock.countdown(CaliControl.deadline)
            if let r = await write(frames, until: left) { return r }
            return await confirm(cmd, pushes: pushes, within: left())
        case .refused(let why): return .refused(why)
        case .elsewhere(let why): return .elsewhere(why)
        case .badValue: return .refused("This value is not valid for the command.")
        case .needsLightConfig: return .refused(CaliControl.wakeupUnknown)
        }
    }

    /// Writes `frames` in order within the time `left` gives; nil when every frame was ACKed, else
    /// the result. A frame to a char this app never writes refuses the whole command before any write.
    func write(_ frames: [ControlFrame], until left: @Sendable () -> Duration) async -> CommandResult? {
        // The satellite's allow-list (cali_ctl_write_ok); the V3 deny-list stays as an extra check.
        if let bad = frames.first(where: { !CaliControl.writeOK($0.char, $0.data.count) || CamperGATT.forbiddenWrites.contains($0.char) }) {
            return .refused(String(format: "This app never writes char %04x.", bad.char))
        }
        var acked = false
        for f in frames {
            if f.delayMs > 0 { try? await clock.sleep(for: .milliseconds(Int(f.delayMs))) }
            let budget = left()
            if budget <= .zero { return .timeout }
            do {
                try await race(clock, budget) { [transport] in try await transport.write(f.char, f.data) }
                acked = true
            } catch SessionError.unreachable {
                return .timeout  // the write hung past the deadline
            } catch {
                // Rejected or not connected: nothing more goes out. After an ACKed frame the unit may have acted.
                return acked ? .unconfirmed : .failed("The camper unit did not take the command.")
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

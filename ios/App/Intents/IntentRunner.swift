import AppIntents
import CaliBLE
import CaliCore
import Foundation

/// What Siri says after an action, and whether the unit confirmed it.
///
/// - Requirement: R_IOS_SIRI_HONEST
struct IntentOutcome {
    let dialog: LocalizedStringResource
    /// The unit confirmed the change (`.applied`).
    let success: Bool
    /// The runner's result; nil when the budget ran out first.
    let result: CommandResult?
}

/// Runs one Siri or Shortcuts command over the session within a time budget, and words the result
/// for speech: "is on/off" only for `.applied`, the app's own texts for everything else.
///
/// - Requirement: R_IOS_SIRI_HONEST, R_IOS_HONEST, R_IOS_POWER_SAVE, R_IOS_SIRI_NO_HEATER
@MainActor
enum IntentRunner {
    /// Runs `cmd` with one connect attempt at most (no redial loop) and answers by `budget`.
    ///
    /// The connect gets `budget` minus the arm delay and 1 s for the frames; when the unit's
    /// reconnect cooldown and a connect do not fit that, the session answers "try again" at once.
    /// When the budget ends first, the command keeps running in the session (it is never cancelled
    /// between its arm delay and its frames). Siri then says "sent, but not confirmed in time" when a
    /// frame went out, else "did not answer in time"; `onResult` gets the real result when it comes.
    /// An air-heater command is refused before anything is sent (no air heater by voice).
    static func perform(_ cmd: Command, session: CamperSession, budget: Duration = .seconds(9),
                        onResult: (@MainActor (CommandResult) -> Void)? = nil) async -> IntentOutcome {
        if cmd.function == "air" + "heater" {
            onResult?(.refused(.elsewhere))
            return outcome(.refused(.elsewhere), cmd)
        }
        let connect = max(budget - CamperGATT.armDelay - .seconds(1), .seconds(1))
        let (results, sink) = AsyncStream<CommandResult?>.makeStream()
        Task {
            let r = await session.run(cmd, connectTimeout: connect)
            sink.yield(r)
            onResult?(r)
        }
        let timer = Task { try? await Task.sleep(for: budget); sink.yield(nil) }
        var first: CommandResult?
        for await r in results { first = r; break }
        timer.cancel()
        sink.finish()
        if let first { return outcome(first, cmd) }
        let dialog: LocalizedStringResource = await session.commandIssuedFrame
            ? "\(ResultBanner.title(cmd.function)): sent, but the camper unit did not confirm in time"
            : ResultBanner.text(.timeout, function: cmd.function)
        return IntentOutcome(dialog: dialog, success: false, result: nil)
    }

    /// The spoken answer to `r`.
    static func outcome(_ r: CommandResult, _ cmd: Command) -> IntentOutcome {
        let dialog: LocalizedStringResource = switch r {
        case .applied: applied(cmd)
        // For lighting only the lamp itself is proof (the state char is a write-through echo).
        case .sent where cmd.function == "lighting": "Lighting: sent, check the lamp"
        case .sent, .unconfirmed: "\(ResultBanner.title(cmd.function)): sent, not confirmed"
        // The app's cooldown text, with the unit spoken as a word.
        case .failed(.resting(let s)): "The camper unit saves power — try again in \(s) seconds"
        default: ResultBanner.text(r, function: cmd.function)
        }
        return IntentOutcome(dialog: dialog, success: r == .applied, result: r)
    }

    private static func applied(_ cmd: Command) -> LocalizedStringResource {
        let on = cmd.value == "on"
        switch (cmd.function, cmd.what) {
        case ("campingmode", "master"): return on ? "Camping mode is on" : "Camping mode is off"
        case ("lighting", "power"): return on ? "All lights are on" : "All lights are off"
        case ("lighting", "profile"):
            let label = LightsView.favourites.first { String($0.profile) == cmd.value }?.label ?? cmd.value ?? ""
            return "Favourite \(label) is on"
        case ("cooler", "power"): return on ? "The cooler is on" : "The cooler is off"
        default: return "Applied"
        }
    }

    /// An intent's `perform`: the app's one session, the answer as Siri's dialog, and the result in
    /// the app's banner (when the app is open, the user sees what Siri did).
    static func run(_ cmd: Command) async -> some IntentResult & ProvidesDialog {
        let store = CamperStore.shared
        store.show(.sending(function: cmd.function), clearAfter: nil)
        // The banner shows the real result when it comes, also after Siri answered.
        let outcome = await perform(cmd, session: store.session) { store.show(.result($0, function: cmd.function), clearAfter: 8) }
        return .result(dialog: IntentDialog(outcome.dialog))
    }
}

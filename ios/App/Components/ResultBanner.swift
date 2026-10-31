import CaliBLE
import CaliCore
import SwiftUI

/// What became of the last command, in the web UI's words (`processQueue` toasts). "Applied" only
/// when the unit's truthful feedback confirmed it; a sent command says what the user can check.
/// Every reason is a catalog text (EN + DE), never the planner's or the runner's English.
///
/// - Requirement: R_IOS_HONEST, R_IOS_V2_ONLY, R_IOS_I18N
struct ResultBanner: View {
    let result: CommandResult
    let function: String

    /// The function's name in the banner ("Lighting: Sent — check the lamp"): the banner shows on
    /// every tab.
    static func title(_ function: String) -> LocalizedStringResource {
        switch function {
        case "campingmode": "Camping mode"
        case "lighting": "Lighting"
        case "cooler": "Cooler"
        case "airheater": "Air heater"
        case "energy": "Energy mode"
        default: LocalizedStringResource(String.LocalizationValue(function))
        }
    }

    static func text(_ r: CommandResult, function: String) -> LocalizedStringResource {
        switch r {
        case .applied: "Applied"
        // For lighting the state char is a write-through echo: only the lamp itself is proof.
        case .sent: function == "lighting" ? "Sent — check the lamp" : "Sent — the unit didn't confirm it"
        case .unconfirmed: "Sent — the unit didn't confirm it"
        case .refused(let why): text(why)
        case .notSupported: "This camper unit is not supported yet"
        case .busy: "The previous command is still running — try again in a moment"
        case .notReady: "Waiting for the camper unit's data"
        case .failed(let why): text(why)
        case .timeout: "The camper unit did not answer in time"
        }
    }

    /// The planner's and the runner's refusals in the web UI's (the unit's) words.
    static func text(_ r: Refusal) -> LocalizedStringResource {
        switch r {
        case .airheaterNotPossible: "Heater deactivated — not while the engine or the auxiliary water heater is running"
        case .airheaterRuntimePermanent: "Not available during continuous heating"
        case .airheaterTimerArmed: "Stop the timer first"
        case .campingNeedsMaster: "Turn camping mode on first"
        case .coolerStateUnknown: "The refrigerator box's settings are not known yet — try again in a moment"
        case .coolerTimerNeedsFridgeOff: "Switch the refrigerator box off first"
        case .energyEcoNeedsSolar: "ECO needs a solar panel"
        case .energyLocked: "The unit currently does not allow changing the energy mode"
        case .favouriteEmpty: "This favourite is empty on the unit"
        case .notOnOff, .badValue: "This value is not valid"
        case .quietNeedsFridgeOn: "Switch the refrigerator box on first"
        case .roofReading: "Only when the pop-up roof is open"
        case .wakeupNoArea: "The wake-up light needs at least one area"
        case .wakeupClock: "The wake-up light needs the time — set it again"
        case .wakeupUnknown: "The unit has not reported its wake-up light settings yet"
        case .elsewhere: "Not available in this app"
        case .noRoof: "This app does not move the roof"
        case .forbiddenWrite: "Not sent: this app does not write this setting"
        case .unknown: "The camper unit refused the command"
        }
    }

    static func text(_ f: Failure) -> LocalizedStringResource {
        switch f {
        case .resting(let s): "The camper unit saves power — try again in \(s) s"
        case .bluetoothOff: "Bluetooth is off or not allowed for this app"
        case .unreachable: "The camper unit is not reachable"
        case .linkEnded: "The camper unit ended the link — nothing was sent"
        case .notTaken: "The camper unit did not take the command"
        }
    }

    var body: some View {
        let text = Text(Self.title(function)) + Text(verbatim: ": ") + Text(Self.text(result, function: function))
        switch result {
        case .applied: Note(text: text, symbol: "checkmark.circle.fill", tint: .onGreen)
        case .sent where function == "lighting": Note(text: text, symbol: "paperplane", tint: .primary)
        // Sent but not confirmed: a warning, as in the web UI, never like "Applied".
        case .sent, .unconfirmed: Note(text: text, symbol: "exclamationmark.circle", tint: .orange)
        case .busy, .notReady: Note(text: text, symbol: "info.circle", tint: .primary)
        default: AlertCard(text: text)
        }
    }
}

/// A neutral card with one symbol and a text.
struct Note: View {
    let text: Text
    let symbol: String
    let tint: Color
    var busy = false

    init(text: Text, symbol: String, tint: Color, busy: Bool = false) {
        (self.text, self.symbol, self.tint, self.busy) = (text, symbol, tint, busy)
    }

    init(text: LocalizedStringResource, symbol: String, tint: Color, busy: Bool = false) {
        self.init(text: Text(text), symbol: symbol, tint: tint, busy: busy)
    }

    var body: some View {
        Label {
            text
        } icon: {
            if busy { ProgressView() } else { Image(systemName: symbol).foregroundStyle(tint) }
        }
        .font(.subheadline)
        .frame(maxWidth: .infinity, minHeight: 44, alignment: .leading)
        .padding(.horizontal, 12)
        .padding(.vertical, 8)
        .background(Color(uiColor: .secondarySystemBackground), in: RoundedRectangle(cornerRadius: 16))
        .accessibilityElement(children: .combine)
    }
}

/// The feedback pinned under the pill: "Sending…", the result, "Connecting…" or the connect outcome
/// (the cooldown counts down).
///
/// - Requirement: R_IOS_HONEST, R_IOS_POWER_SAVE
struct FeedbackBanner: View {
    let feedback: Feedback
    /// The clock of the countdown (the store's, so a snapshot can fix it).
    var clock: () -> Date = Date.init

    /// The text at `now`, without the function's name; nil when there is nothing to say.
    static func text(_ f: Feedback, now: Date) -> LocalizedStringResource? {
        switch f {
        case .sending: "Sending…"
        case .result(let r, let fn): ResultBanner.text(r, function: fn)
        case .connecting(let until?) where until > now: "Connecting in \(max(1, Int(until.timeIntervalSince(now).rounded(.up)))) s…"
        case .connecting: "Connecting…"
        case .connect(.connected): nil
        case .connect(.resting(let until)):
            ResultBanner.text(Failure.resting(retryAfter: max(1, Int(until.timeIntervalSince(now).rounded(.up)))))
        case .connect(.failed(let f)): ResultBanner.text(f)
        }
    }

    /// What VoiceOver announces: the text with the function's name.
    static func spoken(_ f: Feedback, now: Date, locale: Locale) -> String? {
        guard let text = text(f, now: now)?.string(locale) else { return nil }
        switch f {
        case .sending(let fn), .result(_, let fn): return ResultBanner.title(fn).string(locale) + ": " + text
        case .connecting, .connect: return text
        }
    }

    var body: some View {
        switch feedback {
        case .sending(let fn):
            Note(text: Text(ResultBanner.title(fn)) + Text(verbatim: ": ") + Text("Sending…"), symbol: "", tint: .primary, busy: true)
        case .connecting:
            TimelineView(.periodic(from: .now, by: 1)) { _ in
                Note(text: Self.text(feedback, now: clock()) ?? "", symbol: "", tint: .primary, busy: true)
            }
        case .result(let r, let fn): ResultBanner(result: r, function: fn)
        case .connect(.resting):
            TimelineView(.periodic(from: .now, by: 1)) { _ in
                if let t = Self.text(feedback, now: clock()) { Note(text: t, symbol: "timer", tint: .orange) }
            }
        case .connect(.failed(let f)): AlertCard(text: ResultBanner.text(f))
        case .connect(.connected): EmptyView()
        }
    }
}

/// The one alert style: an orange card with a filled triangle. On stale data it is muted like the
/// other stale values.
///
/// - Requirement: R_IOS_LOOK, R_IOS_HONEST
struct AlertCard: View {
    let text: Text
    @Environment(\.pageStale) private var stale

    init(text: Text) { self.text = text }
    init(text: LocalizedStringResource) { self.text = Text(text) }

    var body: some View {
        Label {
            text.foregroundStyle(stale ? Color.staleText : Color.primary)
        } icon: {
            Image(systemName: "exclamationmark.triangle.fill").foregroundStyle(stale ? Color.staleText : Color.orange)
        }
        .font(.subheadline)
        .frame(maxWidth: .infinity, alignment: .leading)
        .padding(12)
        .background((stale ? Color.gray : Color.orange).opacity(0.15), in: RoundedRectangle(cornerRadius: 16))
        .accessibilityElement(children: .combine)
    }
}

/// The power-save rest under the pill: a button that connects again ("tap to connect").
///
/// - Requirement: R_IOS_POWER_SAVE
struct RestCard: View {
    let store: CamperStore

    var body: some View {
        Button { Task { await store.tapToConnect() } } label: {
            // The pill already says why the link is down; the card says what a tap does, and how
            // long the unit's reconnect cooldown still runs.
            TimelineView(.periodic(from: .now, by: 1)) { _ in
                let left: Int? = if case .rest(let until) = store.link, until > store.now() {
                    max(1, Int(until.timeIntervalSince(store.now()).rounded(.up)))
                } else { nil }
                Label { Text(left.map { "Tap to connect (in \($0) s)" } ?? "Tap to connect") } icon: { Image(systemName: "arrow.clockwise") }
                    .font(.subheadline)
                    .frame(maxWidth: .infinity, minHeight: 44, alignment: .leading)
                    .padding(.horizontal, 12)
                    .background(Color.onGreen.opacity(0.12), in: RoundedRectangle(cornerRadius: 16))
            }
        }
        .buttonStyle(.plain)
        .disabled(store.feedback != nil)
    }
}

/// Why the controls are locked (the rest has its `RestCard` under the pill).
///
/// - Requirement: R_IOS_HONEST
struct LockCard: View {
    let reason: LocalizedStringResource

    var body: some View {
        Note(text: reason, symbol: "lock", tint: .primary)
    }
}

/// Sends one command of a control screen: after the prompts (the web UI's confirm dialogs, all in
/// one alert).
struct Run {
    fileprivate let send: (Command, [LocalizedStringResource]) -> Void

    func callAsFunction(_ cmd: Command, confirm: [LocalizedStringResource?] = []) { send(cmd, confirm.compactMap { $0 }) }
}

extension EnvironmentValues {
    /// Why the whole section is locked (the rows give it as their VoiceOver hint); nil = unlocked.
    @Entry var sectionLock: LocalizedStringResource?
}

/// The controls of one function: the lock card when they cannot be used (the controls are then
/// disabled, and each row's hint gives the reason), and the controls. The sending state and the
/// result show pinned under the pill (`StatusPage`).
///
/// - Requirement: R_IOS_HONEST, R_IOS_FUNCTIONS
struct ControlSection<Content: View>: View {
    let store: CamperStore
    let function: String
    @ViewBuilder var content: (Run) -> Content
    @State private var pending: (cmd: Command, prompts: [LocalizedStringResource])?
    @Environment(\.locale) private var locale

    private func send(_ cmd: Command) { Task { await store.run(cmd) } }

    var body: some View {
        let reason = store.lockReason(function)
        VStack(spacing: 12) {
            // An unsupported unit has the page's alert card; the rest has the RestCard under the pill.
            if let reason, store.state.gate == .supported, !store.resting { LockCard(reason: reason) }
            content(Run { cmd, prompts in
                if prompts.isEmpty { send(cmd) } else { pending = (cmd, prompts) }
            })
            .disabled(reason != nil || store.isSending)
            .environment(\.sectionLock, reason)
        }
        .alert(Text(verbatim: pending?.prompts.map { $0.string(locale) }.joined(separator: "\n\n") ?? ""),
               isPresented: Binding(get: { pending != nil }, set: { if !$0 { pending = nil } })) {
            Button("Continue") { if let cmd = pending?.cmd { send(cmd) }; pending = nil }
            Button("Cancel", role: .cancel) { pending = nil }
        }
    }
}

/// A control screen: the status page (pill, V3 card, stale state, feedback) with one function's controls.
///
/// - Requirement: R_IOS_HONEST, R_IOS_V2_ONLY
struct ControlPage<Content: View>: View {
    let store: CamperStore
    let function: String
    let title: LocalizedStringResource
    /// Green marks "on"; amber marks light.
    var tint: Color = .onGreen
    @ViewBuilder var content: (Run) -> Content

    var body: some View {
        StatusPage(store: store) {
            ControlSection(store: store, function: function, content: content)
                .padding(.bottom, 44)  // the last row clears the tab bar's blur band at the end of the scroll
        }
        .tint(tint)
        .navigationTitle(Text(title))
    }
}

/// A grouped card of control rows.
struct Card<Content: View>: View {
    @ViewBuilder var content: () -> Content

    var body: some View {
        VStack(alignment: .leading, spacing: 0) { content() }
            .frame(maxWidth: .infinity, alignment: .leading)
            .background(Color(uiColor: .secondarySystemBackground), in: RoundedRectangle(cornerRadius: 16))
    }
}

/// A control row: the control, and under it why it is disabled (or a note). VoiceOver hears the
/// reason once, as the hint: the row's own, else the section's lock reason.
struct ControlRow<Control: View>: View {
    var disabledBecause: LocalizedStringResource?
    var hint: LocalizedStringResource?
    @ViewBuilder var control: () -> Control
    @Environment(\.sectionLock) private var sectionLock

    var body: some View {
        VStack(alignment: .leading, spacing: 4) {
            control()
                .frame(minHeight: 44)
                .disabled(disabledBecause != nil)
                .accessibilityHint((sectionLock ?? disabledBecause ?? hint).map { Text($0) } ?? Text(verbatim: ""))
            // A locked section has its reason in the lock card; a row note would contradict it.
            if sectionLock == nil, let note = disabledBecause ?? hint {
                Text(note).font(.footnote).accessibilityHidden(true)
            }
        }
        .padding(.horizontal)
        .padding(.vertical, 6)
    }
}

/// A label with a menu picker at the trailing edge (a menu picker outside a form shows no label).
/// Disabled, it shows the current value as text in the muted colour that keeps its contrast.
struct MenuPicker<Value: Hashable, Options: View>: View {
    let label: Text
    /// What VoiceOver calls the picker, when the visible label is not unique.
    var spoken: Text?
    /// The current value as shown, for the disabled state.
    let value: Text
    let selection: Binding<Value>
    @ViewBuilder var options: () -> Options
    @Environment(\.isEnabled) private var enabled

    var body: some View {
        if enabled {
            StackedRow(label: label) {
                Picker(selection: selection, content: options) { spoken ?? label }
                    .pickerStyle(.menu)
                    .labelsHidden()
                    .fixedSize()  // a menu picker alone takes all the height it is offered
            }
        } else {
            StackedRow(label: label) { value.foregroundStyle(Color.staleText) }
                .accessibilityElement(children: .combine)
        }
    }
}

/// A switch with its label; the label stays whole at accessibility text sizes. Disabled, it shows
/// "On" / "Off" as text, so it does not look like an enabled switch that is off.
struct SwitchRow: View {
    let label: Text
    let isOn: Binding<Bool>
    @Environment(\.isEnabled) private var enabled

    var body: some View {
        if enabled {
            StackedRow(label: label) { Toggle(isOn: isOn) { label }.labelsHidden() }
        } else {
            StackedRow(label: label) { Text(isOn.wrappedValue ? "On" : "Off").foregroundStyle(Color.staleText) }
                .accessibilityElement(children: .combine)
        }
    }
}

/// A label and a control side by side; at accessibility text sizes the control goes under the label.
struct StackedRow<Control: View>: View {
    let label: Text
    @ViewBuilder var control: () -> Control
    @Environment(\.dynamicTypeSize) private var typeSize

    var body: some View {
        let layout = typeSize.isAccessibilitySize ? AnyLayout(VStackLayout(alignment: .leading, spacing: 4))
            : AnyLayout(HStackLayout(spacing: 12))
        layout {
            label
            if !typeSize.isAccessibilitySize { Spacer(minLength: 0) }
            control()
        }
        .frame(maxWidth: .infinity, alignment: .leading)
    }
}

extension Command {
    /// `set <function> <what> on|off`.
    static func onOff(_ function: String, _ what: String, _ on: Bool) -> Command {
        Command(function: function, what: what, value: on ? "on" : "off")
    }
}

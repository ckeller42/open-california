import CaliBLE
import SwiftUI

/// What became of the last command, in the web UI's words (`processQueue` toasts). "Applied" only
/// when the unit's truthful feedback confirmed it; a sent command says what the user can check.
///
/// - Requirement: R_IOS_HONEST, R_IOS_V2_ONLY
struct ResultBanner: View {
    let result: CommandResult
    let function: String

    static func isSuccess(_ r: CommandResult) -> Bool { r == .applied }

    static func text(_ r: CommandResult, function: String) -> LocalizedStringResource {
        switch r {
        case .applied: "Applied"
        // For lighting the state char is a write-through echo: only the lamp itself is proof.
        case .sent: function == "lighting" ? "Sent — check the lamp" : "Sent — the unit didn't confirm it"
        case .unconfirmed: "Sent — the unit didn't confirm it"
        // The planner's refusal reasons (calictl's words); a catalog entry translates one.
        case .refused(let why), .elsewhere(let why): LocalizedStringResource(String.LocalizationValue(why))
        case .notSupported: "This camper unit is not supported yet"
        case .busy: "The previous command is still running — try again in a moment"
        case .notReady: "Waiting for the camper unit's data"
        case .failed(let why): "Command failed: \(why)"
        case .timeout: "The camper unit did not answer in time"
        }
    }

    var body: some View {
        let text = Self.text(result, function: function)
        switch result {
        case .applied, .sent, .unconfirmed:
            Label { Text(text) } icon: {
                Image(systemName: result == .applied ? "checkmark.circle.fill" : "paperplane")
                    .foregroundStyle(result == .applied ? Color.onGreen : Color.primary)
            }
            .frame(maxWidth: .infinity, minHeight: 44, alignment: .leading)
            .padding(.horizontal, 12)
            .background(Color(uiColor: .secondarySystemBackground), in: RoundedRectangle(cornerRadius: 16))
            .accessibilityElement(children: .combine)
        default:
            AlertCard(text: text)
        }
    }
}

/// The one alert style: an orange card with a filled triangle. On stale data it is muted like the
/// other stale values.
///
/// - Requirement: R_IOS_LOOK, R_IOS_HONEST
struct AlertCard: View {
    let text: LocalizedStringResource
    @Environment(\.pageStale) private var stale

    var body: some View {
        Label {
            Text(text).foregroundStyle(stale ? Color.staleText : Color.primary)
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

/// Why the controls are locked. In the power-save rest the card is a button that connects again.
///
/// - Requirement: R_IOS_HONEST, R_IOS_POWER_SAVE
struct LockCard: View {
    let store: CamperStore
    let reason: LocalizedStringResource

    var body: some View {
        let label = Label { Text(reason) } icon: { Image(systemName: "lock") }
            .frame(maxWidth: .infinity, minHeight: 44, alignment: .leading)
            .padding(.horizontal, 12)
            .background(Color(uiColor: .secondarySystemBackground), in: RoundedRectangle(cornerRadius: 16))
        if store.resting {
            Button { Task { await store.connect() } } label: { label }
                .buttonStyle(.plain)
        } else {
            label.accessibilityElement(children: .combine)
        }
    }
}

/// Sends one command of a control screen: after the prompts (the web UI's confirm dialogs, all in
/// one alert), with the result under the controls.
struct Run {
    fileprivate let send: (Command, [LocalizedStringResource]) -> Void

    func callAsFunction(_ cmd: Command, confirm: [LocalizedStringResource?] = []) { send(cmd, confirm.compactMap { $0 }) }
}

/// The controls of one function: the lock card when they cannot be used (the controls are then
/// disabled), the controls, and the last command's result.
///
/// - Requirement: R_IOS_HONEST, R_IOS_FUNCTIONS
struct ControlSection<Content: View>: View {
    let store: CamperStore
    let function: String
    @ViewBuilder var content: (Run) -> Content
    @State private var result: CommandResult?
    @State private var sending = false
    @State private var pending: (cmd: Command, prompts: [LocalizedStringResource])?
    @Environment(\.locale) private var locale

    private func send(_ cmd: Command) {
        sending = true
        Task {
            result = await store.send(cmd)
            sending = false
        }
    }

    var body: some View {
        let reason = store.lockReason(function)
        VStack(spacing: 12) {
            // An unsupported unit: the page's alert card already says so.
            if let reason, store.state.gate == .supported { LockCard(store: store, reason: reason) }
            content(Run { cmd, prompts in
                if prompts.isEmpty { send(cmd) } else { pending = (cmd, prompts) }
            })
            .disabled(reason != nil || sending)
            if sending {
                ProgressView { Text("Sending…") }
                    .frame(maxWidth: .infinity, minHeight: 44, alignment: .leading)
            } else if let result {
                ResultBanner(result: result, function: function)
            }
        }
        .alert(Text(verbatim: pending?.prompts.map { $0.string(locale) }.joined(separator: "\n\n") ?? ""),
               isPresented: Binding(get: { pending != nil }, set: { if !$0 { pending = nil } })) {
            Button("Continue") { if let cmd = pending?.cmd { send(cmd) }; pending = nil }
            Button("Cancel", role: .cancel) { pending = nil }
        }
    }
}

/// A control screen: the status page (pill, V3 card, stale state) with one function's controls.
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

/// A control row: the control, and under it why it is disabled (also the VoiceOver hint).
struct ControlRow<Control: View>: View {
    var disabledBecause: LocalizedStringResource?
    var hint: LocalizedStringResource?
    @ViewBuilder var control: () -> Control

    var body: some View {
        VStack(alignment: .leading, spacing: 4) {
            control()
                .frame(minHeight: 44)
                .disabled(disabledBecause != nil)
                .accessibilityHint((disabledBecause ?? hint).map { Text($0) } ?? Text(verbatim: ""))
            if let note = disabledBecause ?? hint {
                Text(note).font(.footnote)
            }
        }
        .padding(.horizontal)
        .padding(.vertical, 6)
    }
}

/// A label with a menu picker at the trailing edge (a menu picker outside a form shows no label).
struct MenuPicker<Value: Hashable, Options: View>: View {
    let label: Text
    /// What VoiceOver calls the picker, when the visible label is not unique.
    var spoken: Text?
    let selection: Binding<Value>
    @ViewBuilder var options: () -> Options

    var body: some View {
        // At accessibility text sizes the control goes under its label, so no word breaks.
        StackedRow(label: label) {
            Picker(selection: selection, content: options) { spoken ?? label }
                .pickerStyle(.menu)
                .labelsHidden()
                .fixedSize()  // a menu picker alone takes all the height it is offered
        }
    }
}

/// A switch with its label; the label stays whole at accessibility text sizes.
struct SwitchRow: View {
    let label: Text
    let isOn: Binding<Bool>

    var body: some View {
        StackedRow(label: label) { Toggle(isOn: isOn) { label }.labelsHidden() }
    }
}

/// A label and a control side by side; at accessibility text sizes the control goes under the label.
/// VoiceOver reads the control (it carries the label).
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

import CaliBLE
import SwiftUI

/// The guided first pairing as steps: (1) open "Gerät verbinden" on the camper screen, search and
/// pick the unit, (2) type the passcode from the camper screen into the iOS dialog, (3) done. Pure:
/// the view feeds it the store's answers, so each step and outcome can be shown and tested without a
/// radio. A paired iPhone sees the guide collapsed to one line and a "Pair again…" button.
///
/// - Requirement: R_IOS_PAIRING
struct PairingGuide: Equatable {
    enum Step: Equatable {
        /// The iPhone is paired: one line and "Pair again…".
        case collapsed
        /// Step 1: open Einstellungen → Bluetooth → Gerät verbinden on the camper screen.
        case openPanel
        case searching
        /// The units found, strongest first: the user picks one (never the app on its own).
        case pick([PairingCandidate])
        /// Step 2 starts: the link to the pick is being made; iOS asks for the passcode next.
        case connecting
        /// Step 2: iOS shows its pairing dialog.
        case typeCode
        /// Step 3: paired now, or the iPhone was paired already (a shared bond).
        case done(PairingOutcome)
        case noUnit(NoUnit)
        /// The pairing did not finish (no answer, a cancelled dialog, a wrong passcode, time ran out).
        case failed
        /// The unit no longer knows this iPhone: Settings → Bluetooth → Forget This Device.
        case forgetDevice

        /// The guide's step, 1 to 3; nil for an outcome that is not a step.
        var number: Int? {
            switch self {
            case .openPanel, .searching, .pick: 1
            case .connecting, .typeCode: 2
            case .done: 3
            case .collapsed, .noUnit, .failed, .forgetDevice: nil
            }
        }

        /// The step waits for the unit or the scan; it has a cancel button.
        var isWaiting: Bool {
            switch self {
            case .searching, .connecting, .typeCode: true
            default: false
            }
        }
    }

    enum NoUnit: Equatable {
        /// Bluetooth is switched off (Control Center).
        case bluetoothOff
        /// The app may not use Bluetooth (Settings).
        case bluetoothDenied
        /// Bluetooth is not usable and the cause is not known: both hints.
        case bluetoothUnknown
        /// No `VWCAMPER` advertised: out of range, or the pairing screen is not open.
        case notFound
        /// A scan already runs.
        case busy

        init(_ a: BluetoothAvailability?) {
            switch a {
            case .poweredOff?: self = .bluetoothOff
            case .unauthorized?: self = .bluetoothDenied
            case .unsupported?, nil: self = .bluetoothUnknown
            }
        }
    }

    private(set) var step: Step
    /// The iPhone is paired: "Close" and "Cancel" return to the collapsed line.
    private var paired = false
    /// "Asks for pairing" answers to a pick: the second one shows the forget-device help.
    private var staleAnswers = 0

    init(step: Step = .openPanel) { self.step = step }

    /// The guide for the app's bond: collapsed when paired; a stale bond (the app paired before)
    /// starts on the help.
    init(bond: Bond) {
        self.init(step: .openPanel)
        self.bond(bond)
    }

    /// The app learned its bond (after a connect): a guide at rest follows it.
    mutating func bond(_ b: Bond) {
        paired = b == .paired
        guard step == .openPanel || step == .collapsed else { return }
        switch b {
        case .paired: step = .collapsed
        case .stale: step = .forgetDevice
        case .notPaired, .unknown: step = .openPanel
        }
    }

    /// "Pair again…" on the collapsed line.
    mutating func expand() { step = .openPanel }

    mutating func search() { step = .searching }

    /// The scan's answer; ignored when the search was cancelled.
    mutating func found(_ r: Result<[PairingCandidate], PairingScanError>) {
        guard step == .searching else { return }
        switch r {
        case .success(let units): step = .pick(units)
        case .failure(.bluetooth(let a)): step = .noUnit(NoUnit(a))
        case .failure(.noUnitFound): step = .noUnit(.notFound)
        case .failure(.busy): step = .noUnit(.busy)
        }
    }

    mutating func picked() { step = .connecting }

    /// The session's link: `.pairing` means iOS shows its dialog (step 2).
    mutating func link(_ l: LinkState) {
        if step == .connecting, l == .pairing { step = .typeCode }
    }

    /// The pick's result; ignored when the user cancelled.
    mutating func finished(_ r: Result<PairingOutcome, SessionError>) {
        guard step == .connecting || step == .typeCode else { return }
        switch r {
        case .success(let o):
            paired = true
            step = .done(o)
        case .failure(.bluetooth(let a)): step = .noUnit(NoUnit(a))
        case .failure(.pairingRequired):
            staleAnswers += 1
            step = staleAnswers >= 2 ? .forgetDevice : .failed
        case .failure: step = .failed
        }
    }

    /// Cancel on a waiting step, or close an outcome: back to the collapsed line when paired, else
    /// to step 1.
    mutating func cancel() { step = paired ? .collapsed : .openPanel }
}

/// The Setup tab's pairing card: the guide's current step with its words and buttons. VoiceOver
/// announces each step change. No identifier, address or signal number is shown: the units differ
/// only by their order (strongest first) and a signal word. The words follow the unit (it shows
/// "Passcode:") and the web UI's pairing wizard.
///
/// - Requirement: R_IOS_PAIRING, R_IOS_I18N, R_IOS_NO_VIN
struct PairingGuideView: View {
    let store: CamperStore
    @State private var guide: PairingGuide
    /// +1 per action: the answer of a cancelled search or pick changes nothing.
    @State private var gen = 0
    @Environment(\.locale) private var locale
    @Environment(\.dynamicTypeSize) private var typeSize
    @Environment(\.openURL) private var openURL

    init(store: CamperStore, guide: PairingGuide? = nil) {
        self.store = store
        _guide = State(initialValue: guide ?? PairingGuide(bond: store.bond))
    }

    /// "No vehicle found": the two reasons the user can check, each its own text.
    static let notFoundChecks: [LocalizedStringResource] = [
        "Stand next to the van: the camper unit must be in range.",
        "Check that “Gerät verbinden” (Connect device) is open on the camper unit.",
    ]

    /// The forget-device help, in the order of the iPhone's own screens.
    static let forgetSteps: [LocalizedStringResource] = [
        "Open Settings → Bluetooth on this iPhone.",
        "Tap the info button next to VWCAMPER, then Forget This Device.",
        "Come back here and pair again.",
    ]

    /// Step 2's instruction, shown from the connect on: the iOS dialog covers the card later.
    static let passcodeHint: LocalizedStringResource = "When iOS asks for a code, type the passcode that the camper screen shows now."
    /// The safety line: the real unit always asks for a passcode (it refuses Just Works).
    static let noPasscodeNotCamper: LocalizedStringResource = "A pairing request without a passcode is not the camper unit: cancel it."
    static let closeGeraetVerbinden: LocalizedStringResource = "You can close “Gerät verbinden” on the camper unit."

    /// The step's headline.
    static func headline(_ step: PairingGuide.Step) -> LocalizedStringResource {
        switch step {
        case .collapsed: "This iPhone is paired with the camper unit."
        case .openPanel: "On the camper unit open Einstellungen → Bluetooth → Gerät verbinden (in English: Settings → Bluetooth → Connect device). It shows “Passcode: ---” until this iPhone connects."
        case .searching: "Searching…"
        case .pick: "Pick your camper unit"
        case .connecting: "Connecting to the unit…"
        case .typeCode: "Type the passcode from the camper screen into the iOS dialog"
        case .done(.paired): "This iPhone is now paired with the camper unit"
        case .done(.alreadyPaired): "This iPhone is already paired with the camper unit"
        case .noUnit(.bluetoothOff): "Bluetooth is off"
        case .noUnit(.bluetoothDenied): "This app may not use Bluetooth"
        case .noUnit(.bluetoothUnknown): "Bluetooth is off or not allowed for this app"
        case .noUnit(.notFound): "No vehicle found."
        case .noUnit(.busy): "A search is already running — wait a moment, then try again."
        case .failed: "Could not connect to the camper unit."
        case .forgetDevice: "The camper unit no longer knows this iPhone"
        }
    }

    /// What VoiceOver announces on a step change: the headline, and from the connect on the
    /// passcode instruction (the iOS dialog takes the focus at step 2).
    static func spoken(_ step: PairingGuide.Step, _ locale: Locale) -> String {
        let head = headline(step).string(locale)
        return step == .connecting ? head + " " + passcodeHint.string(locale) : head
    }

    /// A signal strength in words (never the number).
    static func signal(_ rssi: Int) -> LocalizedStringResource {
        rssi > -65 ? "strong signal" : rssi > -80 ? "medium signal" : "weak signal"
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            if guide.step != .collapsed {
                // At accessibility sizes the step goes under the title, so no word breaks.
                let header = typeSize.isAccessibilitySize ? AnyLayout(VStackLayout(alignment: .leading, spacing: 4))
                    : AnyLayout(HStackLayout(alignment: .firstTextBaseline))
                header {
                    Text("Pair the camper unit").font(.headline)
                        .accessibilityAddTraits(.isHeader)
                    if !typeSize.isAccessibilitySize { Spacer(minLength: 8) }
                    if let n = guide.step.number {
                        Text("Step \(n) of 3").font(.subheadline)
                    }
                }
            }
            content
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .padding()
        .background(Color(uiColor: .secondarySystemBackground), in: RoundedRectangle(cornerRadius: 16))
        .environment(\.pageStale, false)  // the guide's cards are not data: never muted
        .onChange(of: store.link) { _, l in guide.link(l) }
        .onChange(of: store.bond) { _, b in guide.bond(b) }
        .onChange(of: guide.step) { _, step in
            AccessibilityNotification.Announcement(Self.spoken(step, locale)).post()
        }
    }

    @ViewBuilder private var content: some View {
        let step = guide.step
        switch step {
        case .collapsed:
            Label { Text(Self.headline(step)) } icon: { Image(systemName: "checkmark.circle.fill").foregroundStyle(Color.onGreen) }
            action("Pair again…", prominent: false) { guide.expand() }
        case .openPanel:
            Text(Self.headline(step))
            Text("The passcode is valid for about 30 seconds; each attempt shows a new one.").font(.subheadline)
            Text("Close the California On Tour app while pairing — its reconnects can collide with the passkey window.")
                .font(.subheadline)
            action("I'm on that screen", prominent: true) { search() }
        case .searching:
            Label { Text(Self.headline(step)) } icon: { ProgressView() }
            cancelButton
        case .connecting:
            Label { Text(Self.headline(step)) } icon: { ProgressView() }
            Text(Self.passcodeHint).font(.headline)
            Text(Self.noPasscodeNotCamper).font(.subheadline)
            cancelButton
        case .pick(let units):
            Text(Self.headline(step)).font(.subheadline.weight(.semibold))
            Text("The strongest signal is first: the camper unit is the one you are standing next to.")
                .font(.subheadline)
            ForEach(Array(units.enumerated()), id: \.element) { i, unit in
                Button { pick(unit) } label: {
                    Label {
                        VStack(alignment: .leading, spacing: 2) {
                            Text(verbatim: unit.name).font(.body.weight(.semibold))
                                .lineLimit(1).minimumScaleFactor(0.7)  // the name stays one word
                            Text(Self.signal(unit.rssi)).font(.subheadline)
                        }
                    } icon: {
                        Image(systemName: "cellularbars", variableValue: unit.rssi > -65 ? 1 : unit.rssi > -80 ? 0.66 : 0.33)
                    }
                    .frame(maxWidth: .infinity, minHeight: 44, alignment: .leading)
                    .padding(.horizontal, 12)
                    .background(Color(uiColor: .tertiarySystemBackground), in: RoundedRectangle(cornerRadius: 12))
                }
                .buttonStyle(.plain)
                .accessibilityLabel(Text(verbatim: "\(i + 1). \(unit.name), ") + Text(Self.signal(unit.rssi)))
            }
            cancelButton
        case .typeCode:
            Label { Text(Self.headline(step)).font(.headline) } icon: { Image(systemName: "number") }
            Text("Type it within about 30 seconds. Each attempt shows a new passcode.").font(.subheadline)
            Text(Self.noPasscodeNotCamper).font(.subheadline)
            cancelButton
        case .done(let outcome):
            Label { Text(Self.headline(step)) } icon: { Image(systemName: "checkmark.circle.fill").foregroundStyle(Color.onGreen) }
            if outcome == .alreadyPaired {
                Text("This iPhone already has a pairing with the camper unit (for example from the California On Tour app), so no code was needed.").font(.subheadline)
            }
            Text(Self.closeGeraetVerbinden).font(.subheadline)
            action("Close", prominent: false) { guide.cancel() }
        case .noUnit(let why):
            AlertCard(text: Self.headline(step))
            switch why {
            case .notFound:
                ForEach(Self.notFoundChecks.indices, id: \.self) { i in
                    Label { Text(Self.notFoundChecks[i]) } icon: { Image(systemName: i == 0 ? "location" : "gearshape") }
                        .font(.subheadline)
                }
            case .bluetoothOff, .bluetoothDenied, .bluetoothUnknown:
                if why != .bluetoothDenied {
                    Text("Turn on Bluetooth in Control Center, then tap Try again.").font(.subheadline)
                }
                if why != .bluetoothOff {
                    Text("Allow Bluetooth for this app in Settings.").font(.subheadline)
                    action("Open Settings", prominent: false) {
                        if let url = URL(string: UIApplication.openSettingsURLString) { openURL(url) }
                    }
                }
            case .busy:
                EmptyView()
            }
            retryButtons
        case .failed:
            AlertCard(text: Self.headline(step))
            Text("Wrong passcode, or the time ran out. Reopen “Gerät verbinden” on the camper unit and try again: it shows a new passcode.")
                .font(.subheadline)
            retryButtons
        case .forgetDevice:
            AlertCard(text: Self.headline(step))
            ForEach(Self.forgetSteps.indices, id: \.self) { i in
                Text(verbatim: "\(i + 1). ") + Text(Self.forgetSteps[i])
            }
            .font(.subheadline)
            Text("A new pairing also serves the California On Tour app on this iPhone.").font(.subheadline)
            action("Try again", prominent: true) { guide.expand() }
            action("Cancel", prominent: false) { guide.cancel() }
        }
    }

    private var cancelButton: some View {
        action("Cancel", prominent: false) { cancel() }
    }

    @ViewBuilder private var retryButtons: some View {
        action("Try again", prominent: true) { search() }
        action("Cancel", prominent: false) { guide.cancel() }
    }

    private func action(_ title: LocalizedStringResource, prominent: Bool, _ run: @escaping () -> Void) -> some View {
        Button(action: run) {
            Text(title).frame(maxWidth: .infinity, minHeight: 32)
        }
        .buttonStyle(ActionStyle(prominent: prominent))
    }

    private func search() {
        guide.search()
        gen += 1
        let g = gen
        Task {
            let r = await store.pairingCandidates()
            if gen == g { guide.found(r) }
        }
    }

    private func pick(_ unit: PairingCandidate) {
        guide.picked()
        gen += 1
        let g = gen
        Task {
            let r = await store.firstPairing(with: unit.id)
            if gen == g { guide.finished(r) }
        }
    }

    private func cancel() {
        let pairing = guide.step == .connecting || guide.step == .typeCode
        gen += 1
        guide.cancel()
        if pairing { Task { await store.cancelPairing() } }
    }
}

/// The guide's buttons: filled (the next step) or bordered. A deeper blue than the system tint, so
/// the text passes the contrast audit on the card in light and dark.
private struct ActionStyle: ButtonStyle {
    let prominent: Bool
    private static let fill = Color(uiColor: UIColor(red: 0, green: 0.35, blue: 0.78, alpha: 1))
    private static let text = Color(uiColor: UIColor { $0.userInterfaceStyle == .dark
        ? UIColor(red: 0.55, green: 0.75, blue: 1, alpha: 1) : UIColor(red: 0, green: 0.35, blue: 0.78, alpha: 1) })

    func makeBody(configuration: Configuration) -> some View {
        configuration.label
            .font(.body.weight(.semibold))
            .foregroundStyle(prominent ? Color.white : Self.text)
            .padding(.vertical, 6)
            .background(prominent ? Self.fill : Color.clear, in: RoundedRectangle(cornerRadius: 12))
            .overlay(RoundedRectangle(cornerRadius: 12).stroke(Self.text, lineWidth: prominent ? 0 : 1))
            .opacity(configuration.isPressed ? 0.6 : 1)
    }
}

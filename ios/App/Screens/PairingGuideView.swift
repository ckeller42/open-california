import CaliBLE
import SwiftUI

/// The guided first pairing as steps: (1) open "Gerät verbinden" on the camper screen, search and
/// pick the unit, (2) compare the code in the iOS dialog with the camper screen and type it in,
/// (3) done. Pure: the view feeds it the store's answers, so each step and outcome can be shown
/// and tested without a radio.
///
/// - Requirement: R_IOS_PAIRING
struct PairingGuide: Equatable {
    enum Step: Equatable {
        /// Step 1: open Einstellungen → Bluetooth → Gerät verbinden on the camper screen.
        case openPanel
        case searching
        /// The units found, strongest first: the user picks one (never the app on its own).
        case pick([PairingCandidate])
        case connecting
        /// Step 2: iOS shows its pairing dialog.
        case typeCode
        /// Step 3: paired now, or the iPhone was paired already (a shared bond).
        case done(PairingOutcome)
        case noUnit(NoUnit)
        /// The pairing did not finish (no answer, a cancelled dialog, a wrong code).
        case failed
        /// The unit no longer knows this iPhone: Settings → Bluetooth → Forget This Device.
        case forgetDevice

        /// The guide's step, 1 to 3; nil for an outcome that is not a step.
        var number: Int? {
            switch self {
            case .openPanel, .searching, .pick: 1
            case .connecting, .typeCode: 2
            case .done: 3
            case .noUnit, .failed, .forgetDevice: nil
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
        /// Bluetooth is off or not allowed.
        case bluetooth
        /// No `VWCAMPER` advertised: out of range, or the pairing screen is not open.
        case notFound
    }

    private(set) var step: Step
    /// "Asks for pairing" answers to a pick: the second one shows the forget-device help.
    private var staleAnswers = 0

    init(step: Step = .openPanel) { self.step = step }

    /// The guide for the app's bond: a stale bond (the app paired before) starts on the help.
    init(bond: Bond) { self.init(step: bond == .stale ? .forgetDevice : .openPanel) }

    mutating func search() { step = .searching }

    /// The scan's answer; ignored when the search was cancelled.
    mutating func found(_ r: Result<[PairingCandidate], PairingScanError>) {
        guard step == .searching else { return }
        switch r {
        case .success(let units): step = .pick(units)
        case .failure(.bluetooth): step = .noUnit(.bluetooth)
        case .failure(.noUnitFound), .failure(.busy): step = .noUnit(.notFound)
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
        case .success(let o): step = .done(o)
        case .failure(.bluetooth): step = .noUnit(.bluetooth)
        case .failure(.pairingRequired):
            staleAnswers += 1
            step = staleAnswers >= 2 ? .forgetDevice : .failed
        case .failure: step = .failed
        }
    }

    /// Cancel on a waiting step, or close an outcome: back to step 1.
    mutating func cancel() { step = .openPanel }
}

/// The Setup tab's pairing card: the guide's current step with its words and buttons. VoiceOver
/// announces each step change. No identifier, address or signal number is shown: the units differ
/// only by their order (strongest first) and a signal word.
///
/// - Requirement: R_IOS_PAIRING, R_IOS_I18N, R_IOS_NO_VIN
struct PairingGuideView: View {
    let store: CamperStore
    @State private var guide: PairingGuide
    /// +1 per action: the answer of a cancelled search or pick changes nothing.
    @State private var gen = 0
    @Environment(\.locale) private var locale

    init(store: CamperStore, guide: PairingGuide? = nil) {
        self.store = store
        _guide = State(initialValue: guide ?? PairingGuide(bond: store.bond))
    }

    /// "No vehicle found": the two reasons the user can check, each its own text.
    static let notFoundChecks: [LocalizedStringResource] = [
        "Stand next to the van: the camper unit must be in range.",
        "Check that “Gerät verbinden” is open on the camper unit.",
    ]

    /// The forget-device help, in the order of the iPhone's own screens.
    static let forgetSteps: [LocalizedStringResource] = [
        "Open Settings → Bluetooth on this iPhone.",
        "Tap the info button next to VWCAMPER, then Forget This Device.",
        "Come back here and pair again.",
    ]

    /// The step's headline: what VoiceOver announces when the step changes.
    static func headline(_ step: PairingGuide.Step) -> LocalizedStringResource {
        switch step {
        case .openPanel: "On the camper unit open Einstellungen → Bluetooth → Gerät verbinden. It shows “Passcode: ---” until this iPhone connects."
        case .searching: "Searching…"
        case .pick: "Pick your camper unit"
        case .connecting: "Connecting to the unit…"
        case .typeCode: "Compare the code with the camper screen, then type it in"
        case .done(.paired): "This iPhone is now paired with the camper unit"
        case .done(.alreadyPaired): "This iPhone is already paired with the camper unit"
        case .noUnit(.bluetooth): "Bluetooth is off or not allowed for this app"
        case .noUnit(.notFound): "No vehicle found."
        case .failed: "Could not connect to the camper unit."
        case .forgetDevice: "The camper unit no longer knows this iPhone"
        }
    }

    /// A signal strength in words (never the number).
    static func signal(_ rssi: Int) -> LocalizedStringResource {
        rssi > -65 ? "strong signal" : rssi > -80 ? "medium signal" : "weak signal"
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            HStack(alignment: .firstTextBaseline) {
                Text("Pair the camper unit").font(.headline)
                    .accessibilityAddTraits(.isHeader)
                Spacer(minLength: 8)
                if let n = guide.step.number {
                    Text("Step \(n) of 3").font(.subheadline)
                }
            }
            content
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .padding()
        .background(Color(uiColor: .secondarySystemBackground), in: RoundedRectangle(cornerRadius: 16))
        .environment(\.pageStale, false)  // the guide's cards are not data: never muted
        .onChange(of: store.link) { _, l in guide.link(l) }
        .onChange(of: guide.step) { _, step in
            AccessibilityNotification.Announcement(Self.headline(step).string(locale)).post()
        }
    }

    @ViewBuilder private var content: some View {
        let step = guide.step
        switch step {
        case .openPanel:
            Text(Self.headline(step))
            action("I'm on that screen", prominent: true) { search() }
        case .searching, .connecting:
            Label { Text(Self.headline(step)) } icon: { ProgressView() }
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
            Label { Text(Self.headline(step)).font(.subheadline.weight(.semibold)) } icon: { Image(systemName: "number") }
            Text("iOS asks for the 6-digit code. A pairing request without a code is not the camper unit: cancel it.")
                .font(.subheadline)
            cancelButton
        case .done:
            Label { Text(Self.headline(step)) } icon: { Image(systemName: "checkmark.circle.fill").foregroundStyle(Color.onGreen) }
            action("Close", prominent: false) { guide.cancel() }
        case .noUnit(let why):
            AlertCard(text: Self.headline(step))
            if why == .notFound {
                ForEach(Self.notFoundChecks.indices, id: \.self) { i in
                    Label { Text(Self.notFoundChecks[i]) } icon: { Image(systemName: i == 0 ? "location" : "gearshape") }
                        .font(.subheadline)
                }
            }
            retryButtons
        case .failed:
            AlertCard(text: Self.headline(step))
            Text("Wrong passcode, or the unit left pairing mode. Reopen “Gerät verbinden” on the unit and try again.")
                .font(.subheadline)
            retryButtons
        case .forgetDevice:
            AlertCard(text: Self.headline(step))
            ForEach(Self.forgetSteps.indices, id: \.self) { i in
                Text(verbatim: "\(i + 1). ") + Text(Self.forgetSteps[i])
            }
            .font(.subheadline)
            action("Try again", prominent: true) { guide.cancel() }
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

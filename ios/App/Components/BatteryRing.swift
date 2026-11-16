import SwiftUI

/// The cockpit's hero: the second battery's level as a ring, with "W · V" under it (the web UI's
/// `wav` order, W first). W = V × A, derived like the web UI's `wav` (the unit reports no battery
/// power). The percentage is the app's display math (a 0–15 level × 10), not a measured percent: it
/// is shown in whole tens, never with decimals or an animation that suggests more precision.
/// Stale data draws the ring grey, the values muted and "as of {age}" under them.
///
/// - Requirement: R_IOS_LOOK, R_IOS_FUNCTIONS, R_IOS_HONEST
struct BatteryRing: View {
    /// `soc2_pct` (level × 10, nil above 10).
    let percent: Double?
    let volts: Double?
    let amps: Double?
    /// When the shown data arrived (`CamperState.asOf`), for "as of {age}" when stale.
    let asOf: Date?
    @ScaledMetric(relativeTo: .largeTitle) private var size: CGFloat = 130
    @Environment(\.locale) private var locale
    @Environment(\.pageNow) private var pageNow
    @Environment(\.pageStale) private var stale

    private var watts: Double? { volts.flatMap { v in amps.map { (v * $0).rounded() } } }

    private var detail: String {
        [watts.map { "\(Int($0))\(nbsp)W" }, volts.map { "\(numberText($0, locale))\(nbsp)V" }]
            .compactMap { $0 }.joined(separator: " · ")
    }

    private var age: TimeInterval? { stale ? asOf.map { (pageNow ?? Date()).timeIntervalSince($0) } : nil }

    private var spokenValue: String {
        let parts = [
            percent.map { "\(Int($0))%" } ?? "—", watts.map { spoken($0, UnitPower.watts, locale) },
            volts.map { spoken($0, UnitElectricPotentialDifference.volts, locale) },
            age.map { LocalizedStringResource("as of \(spokenAge($0, locale))").string(locale) },
        ]
        return parts.compactMap { $0 }.joined(separator: ", ")
    }

    var body: some View {
        VStack(spacing: 6) {
            ZStack {
                Circle().stroke(Color(uiColor: .tertiarySystemFill), lineWidth: 16)
                Circle()
                    .trim(from: 0, to: (percent ?? 0) / 100)
                    .stroke(
                        stale ? Color(uiColor: .systemGray2) : Color.accentColor,
                        style: StrokeStyle(lineWidth: 16, lineCap: .round)
                    )
                    .rotationEffect(.degrees(-90))
                VStack(spacing: 4) {
                    Image(systemName: "minus.plus.batteryblock")
                    Text(percent.map { "\(Int($0))%" } ?? "—")
                        .font(.system(.largeTitle, design: .rounded, weight: .semibold))
                }
                .foregroundStyle(stale ? Color.staleText : Color.primary)
            }
            .frame(width: size, height: size)
            Text("Second battery")
                .font(.headline)
            Text(verbatim: detail)
                .font(.subheadline.monospacedDigit())
                .foregroundStyle(stale ? Color.staleText : Color.primary)
            if let age {
                Label {
                    Text(verbatim: LocalizedStringResource("as of \(ageText(age).string(locale))").string(locale))
                } icon: {
                    Image(systemName: "clock")
                }
                .font(.footnote)
                .foregroundStyle(Color.staleText)
            }
        }
        .padding(.vertical, 8)
        .accessibilityElement(children: .ignore)
        .accessibilityLabel(Text("Second battery"))
        .accessibilityValue(Text(verbatim: spokenValue))
        .accessibilityIdentifier("battery-ring")
    }
}

import SwiftUI

/// The cockpit's hero: the second battery's level as a ring, with "V · W" under the percentage.
/// W = V × A, derived like the web UI's `wav` (the unit reports no battery power).
///
/// - Requirement: R_IOS_LOOK, R_IOS_FUNCTIONS
struct BatteryRing: View {
    /// `soc2_pct` (level × 10, nil above 10).
    let percent: Double?
    let volts: Double?
    let amps: Double?
    @ScaledMetric(relativeTo: .largeTitle) private var size: CGFloat = 150

    private var detail: String {
        let w = volts.flatMap { v in amps.map { "\(Int((v * $0).rounded())) W" } }
        return [volts.map { "\($0.formatted()) V" }, w].compactMap { $0 }.joined(separator: " · ")
    }

    var body: some View {
        VStack(spacing: 10) {
            ZStack {
                Circle().stroke(Color(uiColor: .tertiarySystemFill), lineWidth: 16)
                Circle()
                    .trim(from: 0, to: (percent ?? 0) / 100)
                    .stroke(Color.accentColor, style: StrokeStyle(lineWidth: 16, lineCap: .round))
                    .rotationEffect(.degrees(-90))
                VStack(spacing: 4) {
                    Image(systemName: "minus.plus.batteryblock")
                    Text(percent.map { "\(Int($0))%" } ?? "—")
                        .font(.system(.largeTitle, design: .rounded, weight: .semibold))
                }
            }
            .frame(width: size, height: size)
            Text("Second battery")
                .font(.headline)
            Text(detail)
                .font(.subheadline.monospacedDigit())
        }
        .padding(.vertical, 8)
        .accessibilityElement(children: .ignore)
        .accessibilityLabel(Text("Second battery"))
        .accessibilityValue(Text(verbatim: [percent.map { "\(Int($0))%" } ?? "—", detail].filter { !$0.isEmpty }.joined(separator: ", ")))
        .accessibilityIdentifier("battery-ring")
    }
}

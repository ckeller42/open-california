import SwiftUI
import UIKit

extension Color {
    /// Amber marks light, green marks "on"; each defined for light and dark mode.
    static let lightAmber = Color(uiColor: UIColor { $0.userInterfaceStyle == .dark
        ? UIColor(red: 1.00, green: 0.78, blue: 0.35, alpha: 1) : UIColor(red: 0.60, green: 0.36, blue: 0.00, alpha: 1) })
    static let onGreen = Color(uiColor: UIColor { $0.userInterfaceStyle == .dark
        ? UIColor(red: 0.45, green: 0.92, blue: 0.55, alpha: 1) : UIColor(red: 0.00, green: 0.42, blue: 0.15, alpha: 1) })
}

/// A large cockpit button: an SF Symbol, the function's name and its state (On / Off), tinted when on.
/// Disabled while the controls are not usable (`CamperStore.controlEnabled`).
///
/// - Requirement: R_IOS_LOOK, R_IOS_HONEST
struct ControlButton: View {
    let title: LocalizedStringResource
    let systemImage: String
    let isOn: Bool
    let tint: Color
    let enabled: Bool
    let action: () -> Void

    var body: some View {
        Button(action: action) {
            VStack(alignment: .leading, spacing: 6) {
                HStack {
                    Image(systemName: systemImage)
                        .font(.title2)
                        .foregroundStyle(isOn ? tint : Color.primary)
                    Spacer(minLength: 0)
                    if !enabled { Image(systemName: "lock").font(.footnote) }
                }
                .accessibilityHidden(true)
                Text(title)
                    .font(.headline)
                    .fixedSize(horizontal: false, vertical: true)
                Text(isOn ? "On" : "Off")
                    .font(.subheadline)
                    .foregroundStyle(isOn ? tint : Color.primary)
            }
            .frame(maxWidth: .infinity, alignment: .leading)
            .padding(12)
            .background(isOn ? tint.opacity(0.12) : Color(uiColor: .secondarySystemBackground),
                        in: RoundedRectangle(cornerRadius: 16))
        }
        .buttonStyle(Undimmed())
        .disabled(!enabled)
        .accessibilityLabel(Text(title))
        .accessibilityValue(Text(isOn ? "On" : "Off"))
    }

    /// A disabled button keeps full contrast (dimmed text fails the contrast audit); the lock marks it.
    private struct Undimmed: ButtonStyle {
        func makeBody(configuration: Configuration) -> some View {
            configuration.label.opacity(configuration.isPressed ? 0.7 : 1)
        }
    }
}

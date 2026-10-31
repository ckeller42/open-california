import SwiftUI
import UIKit

extension Color {
    /// Amber marks light, green marks "on"; each defined for light and dark mode.
    static let lightAmber = Color(
        uiColor: UIColor {
            $0.userInterfaceStyle == .dark
                ? UIColor(red: 1.00, green: 0.78, blue: 0.35, alpha: 1) : UIColor(red: 0.60, green: 0.36, blue: 0.00, alpha: 1)
        })
    static let onGreen = Color(
        uiColor: UIColor {
            $0.userInterfaceStyle == .dark
                ? UIColor(red: 0.45, green: 0.92, blue: 0.55, alpha: 1) : UIColor(red: 0.00, green: 0.42, blue: 0.15, alpha: 1)
        })
}

/// A large cockpit button: an SF Symbol, the function's name and its state (On / Off), tinted when on.
/// While the controls are not usable (`CamperStore.lockReason`) a lock shows and VoiceOver gives the
/// reason as the hint; the button still opens the screen (or connects in the power-save rest).
///
/// - Requirement: R_IOS_LOOK, R_IOS_HONEST
struct ControlButton: View {
    let title: LocalizedStringResource
    let systemImage: String
    let isOn: Bool
    let tint: Color
    /// Why the controls are locked; nil = unlocked. The button still opens the screen.
    let lockedBecause: LocalizedStringResource?
    let action: () -> Void

    var body: some View {
        Button(action: action) {
            VStack(alignment: .leading, spacing: 6) {
                HStack {
                    Image(systemName: systemImage)
                        .font(.title2)
                        .foregroundStyle(isOn ? tint : Color.primary)
                    Spacer(minLength: 0)
                    if lockedBecause != nil { Image(systemName: "lock").font(.footnote) }
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
            .background(
                isOn ? tint.opacity(0.12) : Color(uiColor: .secondarySystemBackground),
                in: RoundedRectangle(cornerRadius: 16))
        }
        .buttonStyle(Undimmed())
        .accessibilityLabel(Text(title))
        .accessibilityValue(Text(isOn ? "On" : "Off"))
        .accessibilityHint(lockedBecause.map { Text($0) } ?? Text(verbatim: ""))
    }

    /// No dimming on press-free states: full contrast, the lock marks a locked function.
    private struct Undimmed: ButtonStyle {
        func makeBody(configuration: Configuration) -> some View {
            configuration.label.opacity(configuration.isPressed ? 0.7 : 1)
        }
    }
}

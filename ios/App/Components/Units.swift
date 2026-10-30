import Foundation
import SwiftUI
import UIKit

/// A no-break space: a number and its unit never part at a line break ("11 L", "2 h ago").
let nbsp = "\u{00A0}"

extension EnvironmentValues {
    /// The status page's clock at its last visible change (`StatusPage`); nil outside a page.
    @Entry var pageNow: Date?
    /// The shown data is older than 90 s (`CamperStore.isStale`): values draw in `Color.staleText`.
    @Entry var pageStale = false
    /// "as of 5 hours ago" while the page is stale (nil when fresh): VoiceOver hears it after each value.
    @Entry var pageStaleSpoken: String?
}

extension Color {
    /// Text of stale values: muted, yet with enough contrast for the accessibility audit.
    static let staleText = Color(uiColor: UIColor { $0.userInterfaceStyle == .dark
        ? UIColor(red: 0.64, green: 0.64, blue: 0.67, alpha: 1) : UIColor(red: 0.36, green: 0.36, blue: 0.39, alpha: 1) })
}

/// A number in `locale` ("14.1" / "14,1").
func numberText(_ x: Double, _ locale: Locale) -> String { x.formatted(.number.locale(locale)) }

/// A value with its unit in full words for VoiceOver ("14.1 volts", "14,1 Volt").
///
/// - Requirement: R_IOS_I18N
func spoken(_ value: Double, _ unit: Dimension, _ locale: Locale) -> String {
    Measurement(value: value, unit: unit).formatted(
        .measurement(width: .wide, usage: .asProvided, numberFormatStyle: .number.precision(.fractionLength(0...1)))
            .locale(locale))
}

/// The age of `ageText` in full words for VoiceOver ("5 hours ago", "vor 5 Stunden"), with the same
/// rounding.
///
/// - Requirement: R_IOS_HONEST, R_IOS_I18N
func spokenAge(_ seconds: TimeInterval, _ locale: Locale) -> String {
    let sec = max(0, seconds)
    if sec < 90 { return LocalizedStringResource("just now").string(locale) }
    let m = Int((sec / 60).rounded()), h = Int((Double(m) / 60).rounded())
    let ago = m < 90 ? DateComponents(minute: -m) : h < 48 ? DateComponents(hour: -h) : DateComponents(day: -Int((Double(h) / 24).rounded()))
    let f = RelativeDateTimeFormatter()
    f.unitsStyle = .full
    f.locale = locale
    return f.localizedString(from: ago)
}

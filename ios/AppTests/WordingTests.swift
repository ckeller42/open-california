import Foundation
import Testing

/// The German of shared texts: the VW app's own wording wins (the unit's vocabulary).
///
/// - Test: T_IOS_WORDING
/// - Verifies: R_IOS_I18N
struct WordingTests {
    /// The catalog's German for an English key, read from the source catalog.
    private func german() throws -> (String) -> String? {
        let root = URL(fileURLWithPath: #filePath).deletingLastPathComponent().deletingLastPathComponent()
        let json =
            try JSONSerialization.jsonObject(with: Data(contentsOf: root.appending(path: "App/Localizable.xcstrings")))
            as? [String: Any]
        let strings = try #require(json?["strings"] as? [String: Any])
        return { key in
            ((((strings[key] as? [String: Any])?["localizations"] as? [String: Any])?["de"] as? [String: Any])?["stringUnit"]
                as? [String: Any])?["value"] as? String
        }
    }

    /// Each text that the VW app also shows has the VW app's German; the heater texts name the air heater.
    @Test func vwGermanWins() throws {
        let de = try german()
        let vw = [
            "Battery is not charging. Please reduce power consumption.":
                "Batterie wird nicht geladen. Bitte Stromverbrauch reduzieren.",
            "Charging current is limited. Ambient temperature is too high.":
                "Ladestrom ist begrenzt. Umgebungstemperatur ist zu hoch.",
            "Connecting…": "Verbindung wird aufgebaut…",
            "connecting…": "Verbindung wird aufgebaut…",
            "External power source device not functioning. Please visit a workshop.":
                "Landstromgerät ohne Funktion. Bitte Werkstatt aufsuchen.",
            "External power source is currently unavailable.": "Landstrom ist gerade nicht verfügbar.",
            "Fill level sensor in waste water tank. Please visit a workshop.":
                "Fehler: Füllstandsensor im Abwassertank. Bitte Werkstatt aufsuchen.",
            "General grey water error. Please visit a workshop.": "Allgemeiner Grauwasser-Fehler. Bitte Werkstatt aufsuchen.",
            "Solar inverter not working. Please visit a workshop.": "Solarwandler ohne Funktion. Bitte Werkstatt aufsuchen.",
            "Something wrong with second battery. Please visit a workshop.": "Zweitbatterie. Bitte Werkstatt aufsuchen.",
            "Unknown fresh water error.": "Unbekannter Frischwasser-Fehler.",
        ]
        for (en, expected) in vw { #expect(de(en) == expected, "\(en)") }

        let fault = try #require(de("Auxiliary air heater fault — please contact your workshop"))
        #expect(fault.contains("Luftstandheizung") && !fault.contains("der Standheizung"))
        let off = try #require(de("Heater deactivated — not while the engine or the auxiliary water heater is running"))
        #expect(off.hasPrefix("Luftstandheizung deaktiviert"))
    }
}

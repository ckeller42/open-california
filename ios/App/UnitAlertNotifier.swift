import UserNotifications

/// Posts a local notification for each new unit alert. Injected into `CamperStore` so a test uses a
/// fake and the real one is only wired in the live app.
///
/// - Requirement: R_IOS_ALERTS
protocol UnitAlertNotifier {
    func post(_ alerts: [UnitAlert])
}

/// The real notifier: one local notification per alert, the alert's id as the request identifier so
/// a re-posted alert replaces its earlier notification rather than stacking. Local only — no push,
/// no entitlement; delivery in the background rides the held Bluetooth link (`R_IOS_BACKGROUND_LINK`).
///
/// - Requirement: R_IOS_ALERTS
struct SystemAlertNotifier: UnitAlertNotifier {
    func post(_ alerts: [UnitAlert]) {
        let center = UNUserNotificationCenter.current()
        for alert in alerts {
            let content = UNMutableNotificationContent()
            content.title = String(localized: "Camper alert")
            content.body = String(localized: alert.body)
            content.sound = .default
            center.add(UNNotificationRequest(identifier: alert.id, content: content, trigger: nil))
        }
    }
}

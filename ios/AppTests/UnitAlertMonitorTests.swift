import CaliBLE
import CaliCore
import Foundation
import Testing

@testable import Camper

@MainActor
struct UnitAlertMonitorTests {
    /// The `.faults` fixture's flagged problems become keyed alerts; a healthy unit has none.
    ///
    /// - Test: T_IOS_ALERTS
    /// - Verifies: R_IOS_ALERTS
    @Test func faultsBecomeAlerts() {
        let store = CamperStore.preview(.faults)
        let alerts = UnitAlertMonitor.alerts(store)
        let ids = Set(alerts.map(\.id))
        // .faults seeds two energy faults and the fresh-water pump protection; no cooler/heater.
        #expect(ids.contains("energy:TwoBattNotCharged"))
        #expect(ids.contains("energy:CurrentDeratingTemperature"))
        #expect(ids.contains("water:fresh:pump_protection"))
        #expect(!ids.contains { $0.hasPrefix("cooler:") })
        #expect(!ids.contains { $0.hasPrefix("heater:") })
        #expect(alerts.count == 3)
        // The monitor surfaces exactly what the Energy and Water screens show.
        #expect(alerts.count == EnergyView.faults(store).count + WaterView.alerts(store).count)
        #expect(UnitAlertMonitor.alerts(CamperStore.preview(.parked)).isEmpty)
    }

    /// Review Focus 1: the same active set, read again, has no rising edge.
    ///
    /// - Test: T_IOS_ALERTS_EDGE
    /// - Verifies: R_IOS_ALERTS
    @Test func risingEdgeOnlyOnce() {
        let alerts = UnitAlertMonitor.alerts(CamperStore.preview(.faults))
        #expect(UnitAlertMonitor.rising(from: Set(alerts.map(\.id)), to: alerts).isEmpty)
    }

    /// Review Focus 4: a fault that clears and returns is a rising edge again.
    ///
    /// - Test: T_IOS_ALERTS_EDGE
    /// - Verifies: R_IOS_ALERTS
    @Test func clearedAlertPostsAgain() {
        let alert = UnitAlert(id: "cooler:door", body: "x")
        #expect(UnitAlertMonitor.rising(from: ["cooler:door"], to: [alert]).isEmpty)
        #expect(UnitAlertMonitor.rising(from: [], to: [alert]).map(\.id) == ["cooler:door"])
    }

    /// Review Focus 5: an uninstalled function yields no alert; an energy flag with no text keeps the
    /// screen's fallback body, never an empty one.
    ///
    /// - Test: T_IOS_ALERTS
    /// - Verifies: R_IOS_ALERTS
    @Test func unknownAndUninstalledYieldNoAlert() {
        var st = CamperState()
        st.interpreted["energy"] = ["faults": .array([.string("MadeUpFlag")])]
        st.asOf = Date()
        let store = CamperStore(session: Fixture.parked.makeSession(), state: st, link: .up)
        let alerts = UnitAlertMonitor.alerts(store)
        #expect(alerts == [UnitAlert(id: "energy:MadeUpFlag", body: "Unknown fault of the energy system")])
        #expect(!alerts.contains { $0.id.hasPrefix("water:") })
    }

    /// Enabled: applying a state with new alerts posts them once; applying the same state again posts
    /// nothing.
    ///
    /// - Test: T_IOS_ALERTS_POST
    /// - Verifies: R_IOS_ALERTS
    @Test func applyPostsNewAlerts() {
        let store = CamperStore.preview(.parked)
        let fake = FakeNotifier()
        store.alertNotifier = fake
        store.alertsEnabled = { true }
        store.apply(Fixture.faults.state(now: Date()), .up)
        let expected: Set = ["energy:TwoBattNotCharged", "energy:CurrentDeratingTemperature", "water:fresh:pump_protection"]
        #expect(Set(fake.posted) == expected)
        store.apply(Fixture.faults.state(now: Date()), .up)
        #expect(fake.posted.count == 3)  // the same alerts do not post again
    }

    /// Review Focus 2: the switch turned on while alerts are already active posts nothing for them; a
    /// genuinely new alert after that does post. The disabled apply still tracks the set.
    ///
    /// - Test: T_IOS_ALERTS_POST
    /// - Verifies: R_IOS_ALERTS
    @Test func enablingDoesNotReplayActive() {
        let store = CamperStore.preview(.parked)
        let fake = FakeNotifier()
        store.alertNotifier = fake
        store.alertsEnabled = { false }
        store.apply(Fixture.faults.state(now: Date()), .up)
        #expect(fake.posted.isEmpty)  // off: nothing posts, but the set is tracked
        store.alertsEnabled = { true }
        store.apply(Fixture.faults.state(now: Date()), .up)
        #expect(fake.posted.isEmpty)  // already-active alerts are not replayed
        var st = Fixture.faults.state(now: Date())
        var energy = st.interpreted["energy"] ?? [:]
        if case .array(let flags)? = energy["faults"] { energy["faults"] = .array(flags + [.string("ExtraFlag")]) }
        st.interpreted["energy"] = energy
        store.apply(st, .up)
        #expect(fake.posted == ["energy:ExtraFlag"])  // only the new one
    }

    /// A relaunch restores a state whose faults were already notified last run; the first live apply
    /// of the same faults must not re-post them (the seed in `init`).
    ///
    /// - Test: T_IOS_ALERTS_POST
    /// - Verifies: R_IOS_ALERTS
    @Test func restoredActiveAlertsDoNotRepostOnRelaunch() {
        let restored = Fixture.faults.state(now: Date())
        let store = CamperStore(session: Fixture.faults.makeSession(), state: restored, link: .up)
        let fake = FakeNotifier()
        store.alertNotifier = fake
        store.alertsEnabled = { true }
        store.apply(Fixture.faults.state(now: Date()), .up)  // the first live read: the same faults
        #expect(fake.posted.isEmpty)
    }

    /// Review Focus 3: notification permission denied returns the switch to off; granted keeps it on.
    ///
    /// - Test: T_IOS_ALERTS_AUTH
    /// - Verifies: R_IOS_ALERTS
    @Test func deniedAuthorizationTurnsItOff() {
        var flag = true
        AlertsCard.enable(false, flag: &flag)
        #expect(flag == false)
        flag = true
        AlertsCard.enable(true, flag: &flag)
        #expect(flag == true)
    }
}

/// Records the ids it was asked to post. Only ever called on the main actor (from `apply`).
private final class FakeNotifier: UnitAlertNotifier {
    var posted: [String] = []
    func post(_ alerts: [UnitAlert]) { posted += alerts.map(\.id) }
}

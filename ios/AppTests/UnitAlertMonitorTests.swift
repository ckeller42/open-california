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
}

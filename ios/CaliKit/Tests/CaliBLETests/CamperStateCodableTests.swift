import Foundation
import Testing
@testable import CaliBLE
@testable import CaliCore

struct CamperStateCodableTests {
    /// The state the app saves for a relaunch survives a JSON round trip, with every member.
    ///
    /// - Test: T_IOS_STATE_CODABLE
    /// - Verifies: R_IOS_BACKGROUND_LINK, R_IOS_HONEST
    @Test func stateRoundTripsThroughJSON() throws {
        var st = CamperState()
        st.fields = ["general": ["CommunicationVersion": 2], "water": ["FreshWaterLevel": 29]]
        st.interpreted = ["water": ["installed": .bool(true), "fresh": .object(["liters": .number(29)]), "fresh_alert": .null]]
        st.asOf = Date(timeIntervalSince1970: 1_800_000_000.5)
        st.gate = .unsupported(version: 3)
        st.liveFunctions = ["general"]
        st.semanticsErrors = ["roof": "boom"]
        st.lightConfig = ["Mode": 4]
        st.waterHeld = true
        st.waterGoodAt = Date(timeIntervalSince1970: 1_799_990_000)

        let back = try JSONDecoder().decode(CamperState.self, from: JSONEncoder().encode(st))
        #expect(back == st)
        for gate in [ProtocolGate.supported, .unknown, .unsupported(version: 7)] {
            #expect(try JSONDecoder().decode(ProtocolGate.self, from: JSONEncoder().encode(gate)) == gate)
        }
    }
}

import Testing

@testable import CaliCore

struct ProtocolGateTests {
    /// - Test: T_IOS_V2_GATE
    /// - Verifies: R_IOS_V2_ONLY
    @Test func gate() {
        #expect(ProtocolGate(general: ["CommunicationVersion": 2]) == .supported)
        #expect(ProtocolGate(general: ["CommunicationVersion": 3]) == .unsupported(version: 3))
        #expect(ProtocolGate(general: nil) == .unknown)
        #expect(ProtocolGate(general: [:]) == .unknown)
    }
}

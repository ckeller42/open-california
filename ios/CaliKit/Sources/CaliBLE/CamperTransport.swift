import Foundation

/// What a transport reports to the session layer.
public enum TransportEvent: Sendable {
    case connected, encrypted
    case disconnected(reason: DisconnectReason)
    case notified(char: UInt16, bytes: [UInt8])
    case written(char: UInt16, ok: Bool)
    /// No usable bond (the saved unit's bond is stale: *Forget This Device*): the connect fails.
    case pairingRequired
    /// No unit is adopted yet: the connect fails until the user picks one (`beginFirstPairing`).
    case notPaired
    /// A first pairing is in progress: iOS shows its passkey dialog, and the connect goes on.
    case pairing
    case bluetoothUnavailable(BluetoothAvailability)
}

public enum DisconnectReason: Sendable, Equatable {
    /// The unit ended the link (HCI 0x13), for example the power-save hang-up.
    case remoteHangUp
    case linkLost
    case localCancel
    case failedToConnect
}

public enum BluetoothAvailability: Sendable {
    case poweredOff, unauthorized, unsupported
}

/// The one BLE link to the camper unit. The real CoreBluetooth implementation and the fake
/// both conform; the session and the command runner only see this protocol.
///
/// - Requirement: R_IOS_BLE_DIRECT, R_IOS_PAIRING
public protocol CamperTransport: Sendable {
    /// Single consumer: iterate it for the transport's whole lifetime (events are lost otherwise).
    var events: AsyncStream<TransportEvent> { get }
    func connect() async
    func cancel() async
    func read(_ char: UInt16) async throws -> [UInt8]
    func write(_ char: UInt16, _ bytes: [UInt8]) async throws
    func subscribe(_ chars: [UInt16]) async throws
    /// The units in range for a first pairing, strongest first, for the user to pick.
    /// - Throws: `PairingScanError`.
    func pairingCandidates(window: Duration) async throws -> [PairingCandidate]
    /// The user picked `id`: end the current link, then connect to `id` once, at once (a first
    /// pairing). The session waits for the result.
    func beginFirstPairing(with id: UUID) async
}

public enum TransportError: Error, Equatable, Sendable {
    /// The call was not issued: no link.
    case notConnected
    /// The unit answered the write with an error.
    case writeRejected
    /// The write was issued, then the link went before its ACK: the unit may have acted on it.
    case linkLost
    case noSuchChar(UInt16)
}

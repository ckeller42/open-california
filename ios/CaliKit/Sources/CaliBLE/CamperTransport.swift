/// What a transport reports to the session layer.
public enum TransportEvent: Sendable {
    case connected, encrypted
    case disconnected(reason: DisconnectReason)
    case notified(char: UInt16, bytes: [UInt8])
    case written(char: UInt16, ok: Bool)
    case pairingRequired
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
/// - Requirement: R_IOS_BLE_DIRECT
public protocol CamperTransport: Sendable {
    /// Single consumer: iterate it for the transport's whole lifetime (events are lost otherwise).
    var events: AsyncStream<TransportEvent> { get }
    func connect() async
    func cancel() async
    func read(_ char: UInt16) async throws -> [UInt8]
    func write(_ char: UInt16, _ bytes: [UInt8]) async throws
    func subscribe(_ chars: [UInt16]) async throws
}

public enum TransportError: Error, Equatable, Sendable {
    case notConnected
    case writeRejected
    case noSuchChar(UInt16)
}

import CaliBLE
import CaliCore
import Foundation
import Testing

@testable import Camper

/// The app's trace wiring: the Setup switch (a defaults key) decides whether the store's session is
/// traced.
@MainActor
struct TraceWiringTests {
    /// Polls `cond` until it holds (the session works on real time here).
    private func until(_ what: String, _ cond: () async -> Bool) async throws {
        for _ in 0..<200 {
            if await cond() { return }
            try await Task.sleep(for: .milliseconds(25))
        }
        Issue.record("timed out waiting for \(what)")
    }

    /// Off: a whole fixture session leaves no file. On (during the link): the next event is a line.
    ///
    /// - Test: T_IOS_TRACE_SWITCH
    /// - Verifies: R_BLE_TRACE, R_IOS_TESTED
    @Test func theSwitchDecidesWhetherTheSessionIsTraced() async throws {
        let suite = "trace-\(UUID().uuidString)"
        let defaults = try #require(UserDefaults(suiteName: suite))
        defer { defaults.removePersistentDomain(forName: suite) }
        let file = TraceFile(url: FileManager.default.temporaryDirectory.appending(path: "\(suite).jsonl"))
        let fake = Fixture.parked.makeTransport()
        let store = CamperStore(
            session: CamperSession(
                transport: CamperStore.traced(fake, file: file, defaults: defaults), clock: ContinuousClock()))
        store.start()

        await store.connect()
        try await until("live link") { store.link == .up && store.state.asOf != nil }
        #expect(!file.exists)

        defaults.set(true, forKey: CamperStore.traceKey)
        await fake.pushForeign(char: CamperGATT.stateChars["campingmode"]!, bytes: [0x21])
        try await until("the notify line") { file.exists }
        let text = try String(contentsOf: file.url, encoding: .utf8)
        #expect(text.split(separator: "\n").count == 1)
        #expect(text.hasSuffix(#","ev":"notify","char":"1202","fn":"campingmode","hex":"21"}"# + "\n"))
        await file.delete()
    }
}

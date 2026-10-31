import CaliBLE
import CaliCore
import Foundation
import Testing
@testable import Camper

@MainActor
struct PairingGuideTests {
    static let units = [PairingCandidate(id: UUID(), name: "VWCAMPER", rssi: -58),
                        PairingCandidate(id: UUID(), name: "VWCAMPER", rssi: -81)]

    /// The guide runs in the order of R_IOS_PAIRING: (1) open "Gerät verbinden" on the camper panel,
    /// search, pick the unit; (2) the iOS dialog: compare the code with the camper screen and type
    /// it in; (3) done. An iPhone that is already paired skips step 2. Every waiting step can be
    /// cancelled, and a cancel goes back to step 1.
    ///
    /// - Test: T_IOS_GUIDE_STEPS
    /// - Verifies: R_IOS_PAIRING
    @Test func guideStepsInOrder() {
        var g = PairingGuide()
        var seen = [g.step]
        g.search(); seen.append(g.step)
        g.found(.success(Self.units)); seen.append(g.step)
        g.picked(); seen.append(g.step)
        g.link(.pairing); seen.append(g.step)
        g.finished(.success(.paired)); seen.append(g.step)
        #expect(seen == [.openPanel, .searching, .pick(Self.units), .connecting, .typeCode, .done(.paired)])
        #expect(seen.map(\.number) == [1, 1, 1, 2, 2, 3])
        #expect(seen.map(\.isWaiting) == [false, true, false, true, true, false])

        var shared = PairingGuide()
        shared.search()
        shared.found(.success(Self.units))
        shared.picked()
        shared.link(.up)  // no dialog: the bond is the iPhone's already
        #expect(shared.step == .connecting)
        shared.finished(.success(.alreadyPaired))
        #expect(shared.step == .done(.alreadyPaired))

        for wait in [PairingGuide.Step.searching, .connecting, .typeCode] {
            var c = PairingGuide(step: wait)
            c.cancel()
            #expect(c.step == .openPanel)
        }
    }

    /// No unit in the scan: Bluetooth off, not allowed and unknown each have their own outcome; "no
    /// VWCAMPER found" lists the two checks (in range, the pairing screen open); a scan that already
    /// runs is not "not found". A late scan result after a cancel changes nothing.
    ///
    /// - Test: T_IOS_GUIDE_NO_UNIT
    /// - Verifies: R_IOS_PAIRING, R_IOS_I18N
    @Test func noUnitFoundSaysWhy() {
        var g = PairingGuide()
        g.search()
        g.found(.failure(.noUnitFound))
        #expect(g.step == .noUnit(.notFound))
        g.search()
        g.found(.failure(.bluetooth(.poweredOff)))
        #expect(g.step == .noUnit(.bluetoothOff))
        g.search()
        g.found(.failure(.bluetooth(.unauthorized)))
        #expect(g.step == .noUnit(.bluetoothDenied))
        g.search()
        g.found(.failure(.bluetooth(nil)))
        #expect(g.step == .noUnit(.bluetoothUnknown))
        g.search()
        g.found(.failure(.busy))
        #expect(g.step == .noUnit(.busy))  // not "No vehicle found"
        g.search()
        g.cancel()
        g.found(.success(Self.units))  // the scan of the cancelled search
        #expect(g.step == .openPanel)
        let de = Locale(identifier: "de_DE")
        #expect(PairingGuideView.notFoundChecks.map { $0.string(de) } == [
            "Stell dich neben das Fahrzeug: Die Camper-Einheit muss in Reichweite sein.",
            "Prüfe, dass „Gerät verbinden“ an der Camper-Einheit geöffnet ist.",
        ])
    }

    /// A stale bond: the first "asks for pairing" after a pick says try again; a repeated one
    /// explains Settings → Bluetooth → Forget This Device. A saved unit whose bond is stale (the
    /// app paired before) starts the guide on that help at once.
    ///
    /// - Test: T_IOS_GUIDE_FORGET_DEVICE
    /// - Verifies: R_IOS_PAIRING
    @Test func stalePairingShowsForgetDeviceHelp() {
        var g = PairingGuide()
        for expected in [PairingGuide.Step.failed, .forgetDevice] {
            g.search()
            g.found(.success(Self.units))
            g.picked()
            g.finished(.failure(.pairingRequired))
            #expect(g.step == expected)
        }
        #expect(PairingGuide(bond: .stale).step == .forgetDevice)
        #expect(PairingGuide(bond: .notPaired).step == .openPanel)
        let de = Locale(identifier: "de_DE")
        #expect(PairingGuideView.forgetSteps.map { $0.string(de) }.joined().contains("Dieses Gerät ignorieren"))
    }

    /// A paired iPhone sees the guide collapsed ("Pair again…"); it expands to step 1 on that tap,
    /// and "Close" after a pairing, or "Cancel", returns to the collapsed line. A guide at rest
    /// follows the bond the app learns later.
    ///
    /// - Test: T_IOS_GUIDE_COLLAPSED
    /// - Verifies: R_IOS_PAIRING
    @Test func pairedGuideIsCollapsed() {
        var g = PairingGuide(bond: .paired)
        #expect(g.step == .collapsed)
        #expect(g.step.number == nil)
        g.expand()
        #expect(g.step == .openPanel)
        g.search()
        g.cancel()
        #expect(g.step == .collapsed)

        var first = PairingGuide(bond: .unknown)
        #expect(first.step == .openPanel)
        first.bond(.paired)  // the launch connect came up
        #expect(first.step == .collapsed)
        var fresh = PairingGuide()
        fresh.search()
        fresh.found(.success(Self.units))
        fresh.picked()
        fresh.finished(.success(.paired))
        fresh.cancel()  // "Close"
        #expect(fresh.step == .collapsed)
    }

    /// The store's first pairing over the fake transport: the scan lists the units, the pick
    /// connects and reports the outcome; a stale bond marks the bond stale.
    ///
    /// - Test: T_IOS_STORE_FIRST_PAIRING
    /// - Verifies: R_IOS_PAIRING
    @Test func storePairsThroughTheSession() async throws {
        let fake = FakeCamperTransport(frames: [CamperGATT.stateChars["general"]!: [0x30, 0x34, 0x31, 0x30, 0, 0, 0, 0, 2]])
        let store = CamperStore(session: CamperSession(transport: fake, clock: ContinuousClock()))
        await fake.notPairedOnNextConnect()
        #expect(await store.connect() == .failed(.unreachable))
        #expect(store.bond == .notPaired)
        #expect(await store.pairingCandidates() == .failure(.noUnitFound))
        await fake.setCandidates(Self.units)
        #expect(await store.pairingCandidates() == .success(Self.units))
        #expect(await store.firstPairing(with: Self.units[0].id) == .success(.alreadyPaired))
        #expect(store.bond == .paired)
        await fake.requirePairingOnNextConnect()
        #expect(await store.firstPairing(with: Self.units[0].id) == .failure(.pairingRequired))
        #expect(store.bond == .stale)
    }

    /// No view reads, shows or stores the VIN (char `1002`, never modelled), and no screen shows a
    /// Bluetooth address or peripheral identifier: a static scan of the code (not the comments) of
    /// the app's sources.
    ///
    /// - Test: T_IOS_NO_VIN_FIELD
    /// - Verifies: R_IOS_NO_VIN
    @Test func noViewReadsAVinField() throws {
        let app = URL(fileURLWithPath: #filePath).deletingLastPathComponent().deletingLastPathComponent().appending(path: "App")
        let files = try #require(FileManager.default.enumerator(at: app, includingPropertiesForKeys: nil))
            .compactMap { $0 as? URL }.filter { $0.pathExtension == "swift" }
        #expect(files.count > 10)
        let vin = try Regex(#"VIN|Vin|\bvin\b|0x1002\b"#)
        let shown = try Regex(#"uuidString|\.identifier\b|\\\([^)]*\.(id|rssi)\)|macAddress"#)
        for file in files {
            let src = try String(contentsOf: file, encoding: .utf8)
            for (n, raw) in src.split(separator: "\n", omittingEmptySubsequences: false).enumerated() {
                // Code only: a comment may name the rule ("No VIN", R_IOS_NO_VIN).
                let line = String(raw.split(separator: "//", maxSplits: 1, omittingEmptySubsequences: false)[0])
                #expect(line.firstMatch(of: vin) == nil, "\(file.lastPathComponent):\(n + 1) reads a VIN field: \(line)")
                if file.path.contains("/Screens/") || file.path.contains("/Components/") {
                    #expect(line.firstMatch(of: shown) == nil, "\(file.lastPathComponent):\(n + 1) shows an identifier: \(line)")
                }
            }
        }
        #expect(!CamperGATT.stateChars.values.contains(0x1002))
    }
}

import SwiftUI

/// The Setup tab: the page frame (pill with the clock-driven age, the feedback), the unit's status
/// and the pairing guide.
///
/// - Requirement: R_IOS_PAIRING, R_IOS_FUNCTIONS, R_IOS_HONEST
struct SetupView: View {
    let store: CamperStore
    /// The guide's starting step (snapshots); nil: from the app's bond.
    var guide: PairingGuide?

    var body: some View {
        StatusPage(store: store) {
            DeviceStatusView(store: store)
            PairingGuideView(store: store, guide: guide)
                .padding(.bottom, 44)  // the last button clears the tab bar's blur band
        }
    }
}

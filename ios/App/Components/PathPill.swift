import SwiftUI

/// The link pill at the top of each screen: the link state and, for stale data, "as of {age}".
/// Shown inside a `TimelineView`, so the age and the stale state follow the clock without a frame.
///
/// - Requirement: R_IOS_HONEST, R_IOS_LOOK
struct PathPill: View {
    let store: CamperStore

    var body: some View {
        HStack(spacing: 8) {
            Image(systemName: "dot.radiowaves.left.and.right")
                .foregroundStyle(store.isStale ? Color.secondary : Color.onGreen)
                .accessibilityHidden(true)
            Text(store.linkLabel)
                .font(.subheadline.weight(.semibold))
            if store.isStale, let age = store.age {
                Text("as of \(String(localized: age))")
                    .font(.subheadline)
            }
        }
        .padding(.horizontal, 14)
        .padding(.vertical, 6)
        .background(Color(uiColor: .secondarySystemBackground), in: Capsule())
    }
}

/// The page frame shared by the three status screens: the pill, the protocol gate, then the content.
/// The `TimelineView` redraws it every 15 s, so "as of {age}" and the stale state refresh by themselves.
///
/// - Requirement: R_IOS_HONEST, R_IOS_V2_ONLY
struct StatusPage<Content: View>: View {
    let store: CamperStore
    @ViewBuilder var content: () -> Content

    var body: some View {
        TimelineView(.periodic(from: .now, by: 15)) { _ in
            ScrollView {
                VStack(spacing: 16) {
                    PathPill(store: store)
                    if case .unsupported = store.state.gate {
                        Label("This camper unit is not supported yet", systemImage: "exclamationmark.triangle")
                            .multilineTextAlignment(.center)
                    }
                    content()
                }
                .padding()
            }
        }
    }
}

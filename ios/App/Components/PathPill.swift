import SwiftUI

/// The link pill at the top of each screen: the link state and, for stale data, "as of {age}",
/// both at `now`. `now` is a value, so each `TimelineView` tick gives a new pill (the store alone is
/// the same reference on every tick); the drawn `PillLabel` changes only when its text changes, so a
/// tick without a visible change leaves the accessibility tree alone.
///
/// - Requirement: R_IOS_HONEST, R_IOS_LOOK
struct PathPill: View {
    let store: CamperStore
    let now: Date
    @Environment(\.locale) private var locale

    var body: some View {
        let stale = store.isStale(at: now)
        PillLabel(stale: stale, label: store.linkLabel(at: now).string(locale),
                  asOf: stale ? store.age(at: now).map { LocalizedStringResource("as of \($0.string(locale))").string(locale) } : nil)
    }
}

/// What the pill draws: plain values, so SwiftUI skips it while they stay equal.
struct PillLabel: View, Equatable {
    let stale: Bool
    let label: String
    let asOf: String?

    var body: some View {
        HStack(spacing: 8) {
            Image(systemName: "dot.radiowaves.left.and.right")
                .foregroundStyle(stale ? Color.secondary : Color.onGreen)
                .accessibilityHidden(true)
            Text(verbatim: label)
                .font(.subheadline.weight(.semibold))
            if let asOf {
                Text(verbatim: asOf)
                    .font(.subheadline)
            }
        }
        .padding(.horizontal, 14)
        .padding(.vertical, 6)
        .background(Color(uiColor: .secondarySystemBackground), in: Capsule())
    }
}

/// The page frame shared by the three status screens: the pill, the protocol gate, then the content.
/// The pill ticks every second (it redraws only when its text changes). The content redraws when the
/// stale state flips (its controls disable then), not on every tick: a redraw of the whole page every
/// second broke the accessibility audit's element references. Each tick reads the store's clock
/// (`store.now()`), so a snapshot's fixed clock holds.
///
/// - Requirement: R_IOS_HONEST, R_IOS_V2_ONLY
struct StatusPage<Content: View>: View {
    let store: CamperStore
    @ViewBuilder var content: () -> Content
    /// The stale state as of the last tick; reading it in `body` redraws the content when it flips.
    @State private var stale = false

    var body: some View {
        ScrollView {
            VStack(spacing: 12) {
                TimelineView(.periodic(from: .now, by: 1)) { _ in
                    let now = store.now()
                    PathPill(store: store, now: now)
                        .onChange(of: store.isStale(at: now), initial: true) { _, flipped in stale = flipped }
                }
                if case .unsupported = store.state.gate {
                    Label("This camper unit is not supported yet", systemImage: "exclamationmark.triangle")
                        .font(.subheadline)
                        .multilineTextAlignment(.center)
                }
                VStack(spacing: 12) {
                    let _ = stale
                    content()
                }
            }
            .padding()
        }
    }
}

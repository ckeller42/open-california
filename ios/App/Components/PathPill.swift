import SwiftUI

/// The link pill at the top of each screen: the link state and, for stale data, "as of {age}",
/// both at `now`. `now` is a value, so each `TimelineView` tick gives a new pill (the store alone is
/// the same reference on every tick); the drawn `PillLabel` changes only when its text changes, so a
/// tick without a visible change leaves the accessibility tree alone. VoiceOver reads it as one
/// element with the age in full words ("offline, as of 5 hours ago").
///
/// - Requirement: R_IOS_HONEST, R_IOS_LOOK
struct PathPill: View {
    let store: CamperStore
    let now: Date
    @Environment(\.locale) private var locale

    var body: some View {
        let stale = store.isStale(at: now)
        let label = store.linkLabel(at: now).string(locale)
        let age = stale ? store.state.asOf.map { now.timeIntervalSince($0) } : nil
        PillLabel(stale: stale, label: label,
                  asOf: age.map { LocalizedStringResource("as of \(ageText($0).string(locale))").string(locale) },
                  spoken: age.map { "\(label), " + LocalizedStringResource("as of \(spokenAge($0, locale))").string(locale) } ?? label)
    }
}

/// What the pill draws: plain values, so SwiftUI skips it while they stay equal.
struct PillLabel: View, Equatable {
    nonisolated static func == (a: Self, b: Self) -> Bool { (a.stale, a.label, a.asOf, a.spoken) == (b.stale, b.label, b.asOf, b.spoken) }

    let stale: Bool
    let label: String
    let asOf: String?
    let spoken: String

    @Environment(\.dynamicTypeSize) private var typeSize

    var body: some View {
        // At accessibility sizes the symbol goes above the text, so a long word keeps the width.
        let layout = typeSize.isAccessibilitySize ? AnyLayout(VStackLayout(spacing: 4)) : AnyLayout(HStackLayout(spacing: 8))
        layout {
            Image(systemName: "dot.radiowaves.left.and.right")
                .foregroundStyle(stale ? Color.secondary : Color.onGreen)
            Text(verbatim: label)
                .font(.subheadline.weight(.semibold))
            if let asOf {
                Text(verbatim: asOf)
                    .font(.subheadline)
            }
        }
        .padding(.horizontal, 14)
        .padding(.vertical, 6)
        // A capsule on one line; a rounded card when large text wraps it.
        .background(Color(uiColor: .secondarySystemBackground), in: RoundedRectangle(cornerRadius: 16))
        .accessibilityElement(children: .ignore)
        .accessibilityLabel(Text(verbatim: spoken))
        .accessibilityAddTraits(.isStaticText)
    }
}

/// The page frame shared by the three status screens: the pill, the protocol gate, then the content.
/// The pill ticks every second (it redraws only when its text changes). The content gets the page's
/// clock and stale state through the environment (`pageNow`, `pageStale`), updated only when a
/// visible text changes (the stale flip, a minute of an age), not on every tick: a redraw of the
/// whole page every second broke the accessibility audit's element references. Each tick reads the
/// store's clock (`store.now()`), so a snapshot's fixed clock holds.
///
/// - Requirement: R_IOS_HONEST, R_IOS_V2_ONLY
struct StatusPage<Content: View>: View {
    let store: CamperStore
    @ViewBuilder var content: () -> Content
    /// The clock at the last visible change of the page's texts.
    @State private var changedAt: Date?
    @Environment(\.locale) private var locale

    /// What the page shows of the clock: the stale state and the minute-level ages.
    private func visible(at now: Date) -> [String] {
        [String(store.isStale(at: now))] + [store.state.asOf, store.state.waterGoodAt].map { at in
            at.map { ageText(now.timeIntervalSince($0)).string(locale) } ?? "-"
        }
    }

    var body: some View {
        let now = changedAt ?? store.now()
        ScrollView {
            VStack(spacing: 12) {
                TimelineView(.periodic(from: .now, by: 1)) { _ in
                    let tick = store.now()
                    PathPill(store: store, now: tick)
                        .onChange(of: visible(at: tick), initial: true) { changedAt = tick }
                }
                if case .unsupported = store.state.gate {
                    Label {
                        Text("This camper unit is not supported yet")
                    } icon: {
                        Image(systemName: "exclamationmark.triangle.fill").foregroundStyle(.orange)
                    }
                    .font(.subheadline)
                    .frame(maxWidth: .infinity, alignment: .leading)
                    .padding(12)
                    .background(Color.orange.opacity(0.15), in: RoundedRectangle(cornerRadius: 16))
                }
                VStack(spacing: 12) { content() }
                    .environment(\.pageNow, now)
                    .environment(\.pageStale, store.isStale(at: now))
            }
            .padding()
        }
    }
}

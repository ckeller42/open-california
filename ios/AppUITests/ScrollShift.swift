import CoreGraphics

/// The vertical distance the page's content moved between two frame maps (`"name#n"` → frame), or
/// nil when it did not scroll.
///
/// Only elements in both maps count (the same key, so the same name): a text that changed (an age
/// that ticks, a live value), an element that appeared or went away does not. The content scrolled
/// when at least `minGroup` elements moved by one distance (within 0.5 pt) of at least 1 pt. One
/// element that moves by itself (a banner, a spinner) and sub-point jitter are not a scroll; the
/// bars, which do not move, are not either.
///
/// - Requirement: R_IOS_TESTED
func scrollShift(from a: [String: CGRect], to b: [String: CGRect], minGroup: Int = 3) -> CGFloat? {
    let shifts = a.compactMap { key, frame in b[key].map { $0.minY - frame.minY } }
        .filter { abs($0) >= 1 }
        .sorted()
    // The largest run of shifts that lie within 0.5 pt of each other.
    var best: (count: Int, value: CGFloat) = (0, 0)
    var start = 0
    for end in shifts.indices {
        while shifts[end] - shifts[start] > 0.5 { start += 1 }
        if end - start + 1 > best.count { best = (end - start + 1, shifts[start]) }
    }
    return best.count >= minGroup ? best.value : nil
}

/// The key in `frames` of an audit issue's element (`name` = its identifier, else its label): the
/// element with that name nearest to `frame`. A text inside a combined element (the pill's
/// "offline") has no entry of its own: the smallest element that holds it. `name#?` if neither.
///
/// - Requirement: R_IOS_TESTED
func elementKey(name: String, frame: CGRect, in frames: [String: CGRect]) -> String {
    func distance(_ r: CGRect) -> CGFloat { abs(r.midY - frame.midY) + abs(r.midX - frame.midX) }
    let nearest = frames.filter { $0.key.hasPrefix(name + "#") }.min { distance($0.value) < distance($1.value) }?.key
    let holder = frames.filter { $0.value.contains(frame) }
        .min { $0.value.width * $0.value.height < $1.value.width * $1.value.height }?
        .key
    return nearest ?? holder ?? name + "#?"
}

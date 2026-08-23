import Foundation
import WhoopProtocol

public enum PhysiologicalSteps {
    public enum SleepKind: Sendable { case mainSleep, nap, unclassified }
    public struct SleepBlock: Sendable {
        public let onset: Int
        public let end: Int
        public let id: String
        public let editedOnset: Int?
        public let kind: SleepKind
        public var effectiveOnset: Int { editedOnset ?? onset }
        public init(onset: Int, end: Int, id: String? = nil, editedOnset: Int? = nil,
                    kind: SleepKind = .unclassified) {
            self.onset = onset; self.end = end; self.id = id ?? String(onset)
            self.editedOnset = editedOnset; self.kind = kind
        }
        fileprivate func withKind(_ kind: SleepKind) -> SleepBlock {
            SleepBlock(onset: onset, end: end, id: id, editedOnset: editedOnset, kind: kind)
        }
    }
    public struct CycleBoundary: Equatable, Sendable {
        public let sleepId: String
        public let onset: Int
        public init(sleepId: String, onset: Int) { self.sleepId = sleepId; self.onset = onset }
    }
    public struct CycleWindow: Equatable, Sendable {
        public let sleepId: String; public let onset: Int; public let endExclusive: Int
        public init(sleepId: String, onset: Int, endExclusive: Int) {
            self.sleepId = sleepId; self.onset = onset; self.endExclusive = endExclusive
        }
    }
    public struct OwnerSegment: Equatable, Sendable {
        public let owner: String; public let onset: Int; public let endExclusive: Int
        public init(owner: String, onset: Int, endExclusive: Int) {
            self.owner = owner; self.onset = onset; self.endExclusive = endExclusive
        }
    }
    public struct OwnerCoverage: Equatable, Sendable {
        public let owner: String; public let onset: Int; public let endExclusive: Int; public let priority: Int
        public init(owner: String, onset: Int, endExclusive: Int, priority: Int) {
            self.owner = owner; self.onset = onset; self.endExclusive = endExclusive; self.priority = priority
        }
    }
    private static let minMainSleepSeconds = 3 * 3_600

    public static func classifyForCycle(_ blocks: [SleepBlock], offsetSec: Int,
                                        habitualMidsleepSec: Int?) -> [SleepBlock] {
        guard !blocks.isEmpty else { return [] }
        let explicit = blocks.indices.filter { blocks[$0].kind == .mainSleep }
        let selectable = blocks.indices.filter { blocks[$0].kind != .nap }
        let selected: Set<Int>
        if !explicit.isEmpty {
            selected = Set(explicit)
        } else {
            let nightBlocks = selectable.map { SleepStageTotals.NightBlock(start: blocks[$0].effectiveOnset,
                                                                            end: blocks[$0].end) }
            let eligible = SleepStageTotals.bridgedNightGroups(nightBlocks, offsetSec: offsetSec)
                .filter { group in
                    let total = group.indices.reduce(0) { $0 + max(0, nightBlocks[$1].durationS) }
                    let onset = group.indices.map { nightBlocks[$0].start }.min()
                    return total >= minMainSleepSeconds && onset.map {
                        SleepStageTotals.isOvernightOnset($0, offsetSec: offsetSec)
                    } == true
                }
                .flatMap { $0.indices }
            let candidates = eligible.map { index in
                SleepStageTotals.NightBlock(start: blocks[selectable[index]].effectiveOnset,
                                            end: blocks[selectable[index]].end)
            }
            let picked = SleepStageTotals.mainNightGroupIndices(candidates, offsetSec: offsetSec,
                                                                habitualMidsleepSec: habitualMidsleepSec) ?? []
            selected = Set(picked.map { selectable[eligible[$0]] })
        }
        return blocks.indices.map { blocks[$0].withKind(selected.contains($0) ? .mainSleep : .nap) }
    }

    public static func cycleWindows(_ boundaries: [CycleBoundary], now: Int) -> [CycleWindow] {
        var ids = Set<String>()
        let ordered = boundaries.filter { $0.onset <= now && ids.insert($0.sleepId).inserted }
            .sorted { $0.onset < $1.onset }
        return ordered.indices.compactMap { index in
            let end = index + 1 < ordered.count ? ordered[index + 1].onset : now
            return end > ordered[index].onset
                ? CycleWindow(sleepId: ordered[index].sleepId, onset: ordered[index].onset, endExclusive: end)
                : nil
        }
    }

    /// Kotlin twin: `PhysiologicalSteps.ownerSegmentsFromCoverage`.
    public static func ownerSegmentsFromCoverage(_ window: CycleWindow, coverage: [OwnerCoverage],
                                                 fallbackOwner: String) -> [OwnerSegment] {
        guard window.endExclusive > window.onset else { return [] }
        let clipped = coverage.compactMap { item -> OwnerCoverage? in
            let start = max(window.onset, item.onset), end = min(window.endExclusive, item.endExclusive)
            return end > start ? OwnerCoverage(owner: item.owner, onset: start,
                endExclusive: end, priority: item.priority) : nil
        }
        let seams = Set([window.onset, window.endExclusive] + clipped.flatMap { [$0.onset, $0.endExclusive] }).sorted()
        var result: [OwnerSegment] = [], lastOwner = fallbackOwner
        for index in 0..<(seams.count - 1) {
            let start = seams[index], end = seams[index + 1]
            let owner = clipped.filter { start >= $0.onset && start < $0.endExclusive }
                .sorted { $0.priority == $1.priority ? $0.owner < $1.owner : $0.priority < $1.priority }
                .first?.owner ?? lastOwner
            if let last = result.last, last.owner == owner, last.endExclusive == start {
                result[result.count - 1] = OwnerSegment(owner: owner, onset: last.onset, endExclusive: end)
            } else {
                result.append(OwnerSegment(owner: owner, onset: start, endExclusive: end))
            }
            lastOwner = owner
        }
        return result
    }

    /// Same later-sample attribution, class gate and plausibility gate as the Kotlin twin.
    public static func stepsInCycle(_ samples: [StepSample], onsetInclusive: Int,
                                    endExclusive: Int, sleepSessions: [SleepSession]) -> Int? {
        guard endExclusive > onsetInclusive else { return 0 }
        let sorted = samples.sorted { $0.ts < $1.ts }
        let predecessor = sorted.last { $0.ts < onsetInclusive }
        let relevant = (predecessor.map { [$0] } ?? []) + sorted.filter {
            $0.ts >= onsetInclusive && $0.ts < endExclusive
        }
        guard relevant.count >= 2 else { return nil }
        let counted = SleepAwareStepCounter.count(relevant, sleepSessions: sleepSessions)
        var evaluated = false
        var total = 0
        let hasClasses = StepsCounter.hasActivityClasses(relevant)
        for index in 1..<relevant.count {
            let previous = relevant[index - 1], current = relevant[index]
            guard current.ts >= onsetInclusive, current.ts < endExclusive else { continue }
            evaluated = true
            let delta = (current.counter - previous.counter) & 0xffff
            guard StepsCounter.shouldCountDelta(activityClass: current.activityClass, hasActivityClasses: hasClasses),
                  StepsCounter.isPlausibleDelta(previousTs: previous.ts, currentTs: current.ts, delta: delta)
            else { continue }
            let inSleep = sleepSessions.contains { current.ts >= $0.start && current.ts < $0.end }
            if !inSleep { total += delta }
        }
        // SleepAware's accepted sleep/awake-gap contribution is window-scoped by the caller's samples.
        total += counted.acceptedAwakeGapTicks + counted.acceptedSleepBoutTicks
        return evaluated ? total : nil
    }
}

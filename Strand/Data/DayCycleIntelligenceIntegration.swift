import Foundation
import StrandAnalytics
import WhoopProtocol
import WhoopStore

/// Thin iOS adapter for the platform-neutral physiological day-cycle kernels.
enum DayCycleIntelligenceIntegration {
    static let onsetKey = "day_cycle_onset_ts"

    struct Night {
        let daily: DailyMetric
        let sleeps: [CachedSleepSession]
        let owner: String
    }
    struct Result {
        let stepsByWakeDay: [String: Int]
        let onsetByWakeDay: [String: Int]
        let firstWakeDay: String?
    }

    static func compute(nights: [Night], editedRows: [CachedSleepSession], store: WhoopStore,
                        now: Int, offsetSec: Int, habitualMidsleepSec: Int?, ticksPerStep: Double,
                        mode: DayCycleMode, trace: ((String) -> Void)? = nil) async -> Result {
        guard mode == .sleepOnset else { return Result(stepsByWakeDay: [:], onsetByWakeDay: [:], firstWakeDay: nil) }
        let edits = Dictionary(editedRows.map { ($0.startTs, $0) }, uniquingKeysWith: { first, _ in first })
        var boundaries: [PhysiologicalSteps.CycleBoundary] = []
        var wakeDayById: [String: String] = [:]
        var ownerById: [String: String] = [:]
        var sleepContexts: [SleepSession] = []

        for night in nights {
            let blocks = night.sleeps.map { row -> PhysiologicalSteps.SleepBlock in
                let edit = edits[row.startTs]
                return PhysiologicalSteps.SleepBlock(
                    onset: row.startTs, end: row.endTs, id: "\(night.owner):\(row.startTs)",
                    editedOnset: edit?.effectiveStartTs ?? row.startTsAdjusted
                )
            }
            let classified = PhysiologicalSteps.classifyForCycle(
                blocks, offsetSec: offsetSec, habitualMidsleepSec: habitualMidsleepSec)
            let mains = classified.filter { $0.kind == .mainSleep }
            guard let onset = mains.map(\.effectiveOnset).min(), onset <= now else { continue }
            let id = mains.min(by: { $0.effectiveOnset < $1.effectiveOnset })?.id ?? "\(night.owner):\(onset)"
            boundaries.append(.init(sleepId: id, onset: onset))
            wakeDayById[id] = night.daily.day
            ownerById[id] = night.owner
            for row in night.sleeps {
                let effective = edits[row.startTs] ?? row
                sleepContexts.append(SleepSession(
                    start: effective.effectiveStartTs, end: effective.endTs,
                    efficiency: effective.efficiency ?? 0,
                    stages: AnalyticsEngine.decodeStages(effective.stagesJSON),
                    restingHR: effective.restingHr, avgHRV: effective.avgHrv))
            }
        }

        if let latest = boundaries.max(by: { $0.onset < $1.onset }),
           let wakeDay = wakeDayById[latest.sleepId], let owner = ownerById[latest.sleepId] {
            let active = DayCycleResolver.activeWindow(
                mode: mode,
                latestSleep: DayCycleWindow(id: latest.sleepId, startInclusive: latest.onset,
                                            endExclusive: now, displayDay: wakeDay, source: .detectedSleep),
                now: now, offsetSec: offsetSec, reliableAwakeCoverage: false)
            if active.source == .syntheticMidnight {
                boundaries.append(.init(sleepId: active.id, onset: active.startInclusive))
                wakeDayById[active.id] = active.displayDay
                ownerById[active.id] = owner
            }
        }

        var steps: [String: Int] = [:]
        var onsets: [String: Int] = [:]
        for window in PhysiologicalSteps.cycleWindows(boundaries, now: now) {
            guard let day = wakeDayById[window.sleepId], let owner = ownerById[window.sleepId] else { continue }
            onsets[day] = window.onset
            let hasClasses = (try? await store.hasStepActivityClasses(
                deviceId: owner, from: window.onset, to: window.endExclusive)) ?? false
            let accumulator = SleepAwareStepCounter.Accumulator(
                sleepSessions: sleepContexts, hasActivityClasses: hasClasses)
            var sampleCount = 0
            var pages = 0
            if let predecessor = try? await store.stepSampleBefore(deviceId: owner, before: window.onset) {
                accumulator.acceptPage([predecessor])
                sampleCount += 1
            }
            var cursor = window.onset
            while cursor < window.endExclusive {
                let page = (try? await store.stepSamples(deviceId: owner, from: cursor,
                    to: window.endExclusive - 1, limit: 10_000)) ?? []
                guard !page.isEmpty else { break }
                accumulator.acceptPage(page)
                pages += 1
                sampleCount += page.count
                guard let last = page.last, last.ts >= cursor else { break }
                cursor = last.ts + 1
                if page.count < 10_000 { break }
            }
            let count = accumulator.finish()
            guard sampleCount >= 2 else { continue }
            let ticks = count.totalTicks
            let scaled = Int((Double(ticks) / max(ticksPerStep, 0.5)).rounded())
            steps[day] = scaled
            if let trace {
                let status = window.endExclusive == now ? "active" : "closed"
                trace("stepsCycle wakeDay=\(day) status=\(status) "
                    + "onsetTs=\(window.onset) endTs=\(window.endExclusive) owner=\(owner) pages=\(pages) "
                    + "samples=\(sampleCount) totalTicks=\(ticks) "
                    + "outside=\(count.acceptedOutsideSleepTicks) awakeGap=\(count.acceptedAwakeGapTicks) "
                    + "sleepBout=\(count.acceptedSleepBoutTicks) "
                    + "rejectedIsolatedSleep=\(count.rejectedIsolatedSleepTicks) "
                    + "rejectedClass=\(count.rejectedActivityClassTicks) "
                    + "rejectedImplausible=\(count.rejectedImplausibleTicks) "
                    + "gravitySamples=n/a auxSamples=n/a truncated=false "
                    + "ticksPerStep=\(ticksPerStep) scaledSteps=\(scaled)")
            }
        }
        return Result(stepsByWakeDay: steps, onsetByWakeDay: onsets,
                      firstWakeDay: onsets.keys.min())
    }

    static func applying(_ result: Result, to daily: DailyMetric) -> DailyMetric {
        let established = result.firstWakeDay.map { daily.day >= $0 } ?? false
        let steps = established ? result.stepsByWakeDay[daily.day] : daily.steps
        return DailyMetric(day: daily.day, totalSleepMin: daily.totalSleepMin, efficiency: daily.efficiency,
            deepMin: daily.deepMin, remMin: daily.remMin, lightMin: daily.lightMin,
            disturbances: daily.disturbances, restingHr: daily.restingHr, avgHrv: daily.avgHrv,
            recovery: daily.recovery, strain: daily.strain, exerciseCount: daily.exerciseCount,
            spo2Pct: daily.spo2Pct, skinTempDevC: daily.skinTempDevC, respRateBpm: daily.respRateBpm,
            steps: steps, activeKcalEst: daily.activeKcalEst, spo2Red: daily.spo2Red,
            spo2Ir: daily.spo2Ir, avgSdnn: daily.avgSdnn)
    }
}

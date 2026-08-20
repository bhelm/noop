import Foundation
import WhoopProtocol

/// Wrap-aware step derivation from the strap's cumulative `step_motion_counter@57`, shared by the daily
/// total (`AnalyticsEngine.analyzeDay`) and any windowed total (a manual workout's `[start, end]`, #398).
///
/// `step_motion_counter@57` is a CUMULATIVE u16 motion counter: it climbs for both locomotion and some
/// non-step wrist motion, and wraps at 65536. On classed WHOOP 5/MG records, the increment ending at each
/// sample is counted only when the strap labels that sample walk (1) or run (2); still (0) and unknown are
/// rejected. A wholly unclassed legacy window retains the old counter-only estimate so pre-migration history
/// remains readable. The caller applies its per-user `stepTicksPerStep` calibration afterwards. The result is
/// still an estimate, not cloud/clinical parity.
///
/// Kept byte-for-byte in lockstep with the Kotlin twin `StepsCounter.stepsInWindow`.
public enum StepsCounter {
    private static let locomotionActivityClasses: Set<Int> = [1, 2]

    static func hasActivityClasses(_ samples: [StepSample]) -> Bool {
        samples.contains { $0.activityClass != nil }
    }

    static func shouldCountDelta(activityClass: Int?, hasActivityClasses: Bool) -> Bool {
        !hasActivityClasses || activityClass.map(locomotionActivityClasses.contains) == true
    }

    /// The largest wrap-aware increment treated as real motion between two adjacent 1 Hz records. A delta
    /// at/above this is a big time-gap / disconnect boundary between sync sessions (or a firmware reboot,
    /// byte-indistinguishable from a u16 wrap), NOT real steps — dropped so gaps don't inflate the total.
    /// Real 1 Hz motion never ticks this fast between adjacent records. (#132/#276/#316)
    public static let maxStepDelta = 512

    /// Raw wrap-aware locomotion-tick total across `samples`. When any sample carries `activityClass`, each
    /// positive increment is attributed to the later sample and retained only for walk/run. When the whole
    /// window is legacy-unclassed, all valid increments retain the historical counter-only fallback. Sorts
    /// by `ts` internally and returns `nil` for fewer than two samples or no retained movement.
    public static func stepsInWindow(_ samples: [StepSample]) -> Int? {
        let sorted = samples.sorted { $0.ts < $1.ts }
        if sorted.count < 2 { return nil }
        let hasActivityClasses = hasActivityClasses(sorted)
        var total = 0
        for i in 1..<sorted.count {
            let delta = (sorted[i].counter - sorted[i - 1].counter) & 0xFFFF  // wrap-aware u16 increment
            let isLocomotion = shouldCountDelta(
                activityClass: sorted[i].activityClass,
                hasActivityClasses: hasActivityClasses)
            if isLocomotion && delta >= 1 && delta < maxStepDelta { total += delta }  // >=512 is a gap/reset
        }
        return total > 0 ? total : nil
    }
}

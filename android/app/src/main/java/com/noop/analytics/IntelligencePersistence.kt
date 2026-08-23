package com.noop.analytics

import com.noop.data.DailyMetric
import com.noop.data.MetricSeriesRow
import com.noop.data.ScoreInputProvenanceRow
import com.noop.data.SleepSession
import com.noop.data.WhoopRepository

/** Persistence-only helpers kept out of the already large scoring orchestrator. */
internal object IntelligencePersistence {
    fun scoreProvenance(
        computedId: String,
        dailies: List<DailyMetric>,
        metricRows: List<MetricSeriesRow>,
        ownerByDay: Map<String, String>,
    ): List<ScoreInputProvenanceRow> {
        val byCell = LinkedHashMap<Pair<String, String>, ScoreInputProvenanceRow>()
        for (daily in dailies) {
            val source = ownerByDay[daily.day] ?: continue
            if (daily.recovery != null) {
                byCell[daily.day to "recovery"] = ScoreInputProvenanceRow(
                    computedId, daily.day, "recovery", source,
                )
            }
            if (daily.strain != null) {
                byCell[daily.day to "strain"] = ScoreInputProvenanceRow(
                    computedId, daily.day, "strain", source,
                )
            }
        }
        for (point in metricRows) {
            val source = ownerByDay[point.day] ?: continue
            byCell[point.day to point.key] = ScoreInputProvenanceRow(
                computedId, point.day, point.key, source,
            )
        }
        return byCell.values.toList()
    }

    suspend fun persistDetectedSleepDetails(
        repo: WhoopRepository,
        computedId: String,
        kept: List<SleepSession>,
        scoredNights: List<DayResult>,
    ) {
        // Only kept (not edited/dismissed) sessions receive their per-epoch motion and band-state arrays.
        // Missing streams remain absent rather than being materialised as synthetic zero arrays.
        if (kept.isNotEmpty()) repo.upsertSleepSessions(kept)
        val keptStarts = kept.map { it.startTs }.toHashSet()
        val motionByStart = HashMap<Long, List<Double>>()
        val sleepStateByStart = HashMap<Long, List<Int>>()
        for (result in scoredNights) {
            for ((start, motion) in result.sessionMotionByStart) {
                if (start in keptStarts) motionByStart[start] = motion
            }
            for ((start, states) in result.sessionSleepStateByStart) {
                if (start in keptStarts) sleepStateByStart[start] = states
            }
        }
        for ((start, motion) in motionByStart) repo.persistSessionMotion(computedId, start, motion)
        for ((start, states) in sleepStateByStart) repo.persistSessionSleepState(computedId, start, states)
    }
}

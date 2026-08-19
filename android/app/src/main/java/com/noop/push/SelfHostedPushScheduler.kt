package com.noop.push

import android.content.Context
import androidx.work.BackoffPolicy
import androidx.work.Constraints
import androidx.work.ExistingWorkPolicy
import androidx.work.NetworkType
import androidx.work.OneTimeWorkRequest
import androidx.work.WorkManager
import java.util.concurrent.TimeUnit

/** The only entry point that queues pushes. Unique work serialises every trigger to one worker. */
object SelfHostedPushScheduler {
    internal const val UNIQUE_WORK = "self-hosted-health-push"
    internal const val BACKOFF_SECONDS = 30L
    // A new true offload supersedes stale/running work. Cancellable deterministic requests and exact
    // acks make replay safe, while REPLACE prevents an unbounded chain of rapid offload triggers.
    internal val EXISTING_WORK_POLICY = ExistingWorkPolicy.REPLACE

    fun enqueueAfterSuccessfulOffload(context: Context) = enqueueIfEnabled(context)

    fun enqueueLaunchCatchUp(context: Context) = enqueueIfEnabled(context)

    fun cancel(context: Context) {
        PushRunSignal.clear(context)
        WorkManager.getInstance(context.applicationContext).cancelUniqueWork(UNIQUE_WORK)
    }

    private fun enqueueIfEnabled(context: Context) {
        val app = context.applicationContext
        val settings = SelfHostedPushSettings.from(app)
        if (settings.enabledEndpoint() == null) return
        val request = OneTimeWorkRequest.Builder(SelfHostedPushWorker::class.java)
            .setConstraints(Constraints.Builder().setRequiredNetworkType(NetworkType.CONNECTED).build())
            .setBackoffCriteria(BackoffPolicy.EXPONENTIAL, BACKOFF_SECONDS, TimeUnit.SECONDS)
            .build() // Deliberately no input Data: credentials and endpoint never enter Worker metadata.
        if (!PushRunSignal.reserve(app, request.id.toString())) return
        try {
            WorkManager.getInstance(app).enqueueUniqueWork(UNIQUE_WORK, EXISTING_WORK_POLICY, request)
        } catch (t: Throwable) {
            PushRunSignal.releaseReservation(app, request.id.toString())
            throw t
        }
    }
}

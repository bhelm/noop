package com.noop.push

import android.content.Context
import android.net.ConnectivityManager
import android.net.NetworkCapabilities
import androidx.work.CoroutineWorker
import androidx.work.WorkerParameters
import com.noop.data.WhoopDatabase
import kotlinx.coroutines.CancellationException

/** One bounded coordinator run. Unique WorkManager work and the trigger lease keep it serial. */
class SelfHostedPushWorker(
    appContext: Context,
    params: WorkerParameters,
) : CoroutineWorker(appContext, params) {
    private data class Decision(val result: Result, val willRetry: Boolean)

    override suspend fun doWork(): Result {
        val settings = SelfHostedPushSettings.from(applicationContext)
        // A stale request after disable exits before lease writes, Wi-Fi, Keystore, Room, or network.
        val requestId = id.toString()
        if (settings.enabledEndpoint() == null) {
            PushRunSignal.releaseReservation(applicationContext, requestId)
            return Result.success()
        }
        var ownerFinished = false
        try {
            PushRunSignal.begin(applicationContext, requestId)
            var decision = try {
                when (val outcome = PushWorkerGate.run(
                    enabledEndpoint = settings::enabledEndpoint,
                    wifiAvailable = ::isOnWifi,
                    token = settings::token,
                    execute = { endpoint, token -> executeOnce(settings, endpoint, token) },
                )) {
                    PushWorkerGate.Outcome.DisabledOrInvalid,
                    PushWorkerGate.Outcome.MissingToken -> Decision(Result.success(), false)
                    PushWorkerGate.Outcome.NotOnWifi -> retryOrStop(settings)
                    is PushWorkerGate.Outcome.Executed ->
                        if (outcome.retry) retryOrStop(settings) else Decision(Result.success(), false)
                }
            } catch (cancelled: CancellationException) {
                throw cancelled
            } catch (_: Throwable) {
                // Deliberately generic: exception strings from TLS/HTTP stacks may include destination data.
                settings.recordError("Push could not start and will retry.")
                Decision(Result.retry(), true)
            }
            val pendingTrigger = PushRunSignal.finish(
                applicationContext, requestId, willRetry = decision.willRetry,
            )
            ownerFinished = true
            if (pendingTrigger && !decision.willRetry) decision = Decision(Result.retry(), true)
            return decision.result
        } finally {
            if (!ownerFinished) runCatching {
                PushRunSignal.finish(applicationContext, requestId, willRetry = false)
            }
        }
    }

    private suspend fun executeOnce(
        settings: SelfHostedPushSettings,
        endpoint: PushEndpointPolicy.ValidEndpoint,
        token: String,
    ): Boolean {
        val sourceId = settings.sourceId()
        // Derive progress from the exact endpoint captured by the stale-work gate. Re-reading prefs
        // here could otherwise pair an E1 HTTP request with E2 cursor state during a concurrent edit.
        val namespace = settings.progressNamespace(sourceId, endpoint)
        // Room is first opened here, after the stale-work, endpoint, Wi-Fi, token and identity gates.
        val dao = WhoopDatabase.get(applicationContext).pushDao()
        val progress = EndpointScopedProgressStore(
            SharedPrefsPushProgressStore.from(applicationContext),
            namespace,
        )
        val startDeviceIndex = settings.nextDeviceIndex(namespace)
        val run = PushCoordinator(
            source = dao,
            transport = PushHttpTransport(endpoint, token),
            progress = progress,
            sourceId = sourceId,
        ).pushKnownDevices(startDeviceIndex, MAX_DEVICES_PER_RUN)
        settings.saveNextDeviceIndex(namespace, run.nextDeviceIndex)
        val cycleNeedsAnotherPass = settings.cycleNeedsAnotherPass(namespace) ||
            run.hasRetryableFailure || run.hasMoreAppendRows
        val cycleHadRejection = settings.cycleHadRejection(namespace) || run.rejectedBatches > 0
        val cycleCompleted = run.nextDeviceIndex == 0
        settings.saveCycleNeedsAnotherPass(namespace, if (cycleCompleted) false else cycleNeedsAnotherPass)
        settings.saveCycleHadRejection(namespace, if (cycleCompleted) false else cycleHadRejection)

        return when {
            !cycleCompleted || cycleNeedsAnotherPass -> {
                settings.recordError("Push is incomplete and will retry.")
                true
            }
            cycleHadRejection -> {
                settings.recordError("The endpoint rejected one or more batches.")
                false
            }
            else -> {
                settings.recordSuccess()
                false
            }
        }
    }

    private fun isOnWifi(): Boolean {
        val connectivity = applicationContext.getSystemService(ConnectivityManager::class.java)
            ?: return false
        val network = connectivity.activeNetwork ?: return false
        val capabilities = connectivity.getNetworkCapabilities(network) ?: return false
        return capabilities.hasTransport(NetworkCapabilities.TRANSPORT_WIFI)
    }

    private fun retryOrStop(settings: SelfHostedPushSettings): Decision =
        if (runAttemptCount + 1 >= MAX_ATTEMPTS) {
            settings.recordError("Push paused after repeated failures; the next sync can try again.")
            Decision(Result.failure(), false)
        } else {
            Decision(Result.retry(), true)
        }

    private companion object {
        const val MAX_DEVICES_PER_RUN = 1
        const val MAX_ATTEMPTS = 32
    }
}

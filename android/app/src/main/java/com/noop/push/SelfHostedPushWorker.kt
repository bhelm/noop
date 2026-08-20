package com.noop.push

import android.content.Context
import android.net.ConnectivityManager
import android.net.NetworkCapabilities
import androidx.work.CoroutineWorker
import androidx.work.ListenableWorker
import androidx.work.WorkerParameters
import com.noop.data.WhoopDatabase
import kotlinx.coroutines.CancellationException

internal fun persistedDeviceIndex(startDeviceIndex: Int, nextDeviceIndex: Int, retryableFailure: Boolean): Int =
    if (retryableFailure) startDeviceIndex else nextDeviceIndex
internal const val PUSH_MAX_ATTEMPTS = 32
internal fun shouldRetryPush(runAttemptCount: Int): Boolean = runAttemptCount + 1 < PUSH_MAX_ATTEMPTS
internal fun resultAfterScheduledContinuation(
    current: ListenableWorker.Result,
    scheduled: Boolean,
): ListenableWorker.Result = if (scheduled) ListenableWorker.Result.success() else current
internal fun successorOwnsEnqueueFailure(currentRequestCouldReserve: Boolean): Boolean =
    !currentRequestCouldReserve
internal fun shouldScheduleLatePendingSuccessor(willRetry: Boolean, settlementPending: Boolean): Boolean =
    !willRetry && settlementPending

/** One bounded coordinator run. Unique WorkManager work and the trigger lease keep it serial. */
class SelfHostedPushWorker(
    appContext: Context,
    params: WorkerParameters,
) : CoroutineWorker(appContext, params) {
    private data class Decision(
        val result: Result,
        val willRetry: Boolean,
        val continueNormally: Boolean = false,
        val status: Status = Status.NONE,
        val message: String? = null,
    )

    private enum class Execution { COMPLETE, CONTINUE, RETRY_FAILURE, TERMINAL_FAILURE }
    private enum class Status { NONE, SUCCESS, CONTINUING, RETRYING, FAILED }

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
            settings.recordRunning()
            var execution = Execution.COMPLETE
            var decision = try {
                when (val outcome = PushWorkerGate.run(
                    enabledEndpoint = settings::enabledEndpoint,
                    wifiAvailable = ::isOnWifi,
                    token = settings::token,
                    execute = { endpoint, token ->
                        execution = executeOnce(settings, endpoint, token)
                        execution == Execution.RETRY_FAILURE
                    },
                )) {
                    PushWorkerGate.Outcome.DisabledOrInvalid -> Decision(Result.success(), false)
                    PushWorkerGate.Outcome.MissingToken -> Decision(
                        Result.failure(), false, status = Status.FAILED,
                        message = "The saved bearer token is unavailable; save it again.",
                    )
                    PushWorkerGate.Outcome.NotOnWifi -> retryOrStop("Waiting for Wi-Fi; push will retry.")
                    is PushWorkerGate.Outcome.Executed -> when {
                        outcome.retry -> retryOrStop("The endpoint or network failed; push will retry.")
                        execution == Execution.CONTINUE -> Decision(
                            Result.success(), false, continueNormally = true, status = Status.CONTINUING,
                        )
                        execution == Execution.TERMINAL_FAILURE -> Decision(
                            Result.failure(), false, status = Status.FAILED,
                            message = "The endpoint rejected one or more batches.",
                        )
                        else -> Decision(Result.success(), false, status = Status.SUCCESS)
                    }
                }
            } catch (cancelled: CancellationException) {
                throw cancelled
            } catch (_: Throwable) {
                // Deliberately generic: exception strings from TLS/HTTP stacks may include destination data.
                retryOrStop("Push could not start and will retry.")
            }
            val settlement = PushRunSignal.settle(
                applicationContext, requestId, willRetry = decision.willRetry,
            ) { pending -> recordSettledStatus(settings, decision, pending) }
            ownerFinished = true
            // Healthy pagination/device rotation is a fresh successful work item. This deliberately
            // avoids WorkManager retry/backoff, which is reserved for real network/HTTP failures.
            val needsContinuation = !decision.willRetry && (decision.continueNormally || settlement.pending)
            if (needsContinuation && !SelfHostedPushScheduler.enqueueContinuation(applicationContext)) {
                // The append Operation itself failed asynchronously. Re-arm THIS WorkRequest so
                // WorkManager's bounded runAttemptCount/backoff handles the infrastructure failure.
                val enqueueRetry = retryOrStop("Could not queue the next push slice; will retry.")
                val currentCouldReserve = PushRunSignal.reserve(applicationContext, requestId)
                if (successorOwnsEnqueueFailure(currentCouldReserve)) {
                    // The preserved pending trigger already owns QUEUED work. Do not let this old
                    // failure (especially terminal attempt 31) overwrite its status or block it.
                    return Result.success()
                }
                PushRunSignal.begin(applicationContext, requestId)
                val enqueueFailureSettlement = PushRunSignal.settle(
                    applicationContext, requestId, willRetry = enqueueRetry.willRetry,
                ) { pending -> recordSettledStatus(settings, enqueueRetry, pending) }
                if (shouldScheduleLatePendingSuccessor(
                        enqueueRetry.willRetry,
                        enqueueFailureSettlement.pending,
                    )
                ) {
                    val scheduled = SelfHostedPushScheduler.enqueueContinuation(
                        applicationContext,
                        preserveTriggerOnFailure = true,
                    )
                    // A terminal attempt must succeed only when its APPEND prerequisite was actually
                    // installed (or another owner won inside enqueueContinuation). No retry reset.
                    return resultAfterScheduledContinuation(enqueueRetry.result, scheduled)
                }
                return enqueueRetry.result
            }
            // APPEND successors depend on this WorkSpec succeeding. A fresh pending trigger must not
            // be left BLOCKED behind a terminal failure from the snapshot that preceded that trigger.
            return resultAfterScheduledContinuation(decision.result, scheduled = needsContinuation)
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
    ): Execution {
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
        settings.recordAcceptedBatches(
            run.acceptedBatches,
            records = run.acceptedRecords.toLong(),
        )
        if (run.hasRetryableFailure) {
            // Do not rotate away from a failing device: this WorkRequest retries the exact same
            // device with its bounded runAttemptCount. Already-acked tables remain idempotent.
            return Execution.RETRY_FAILURE
        }
        settings.saveNextDeviceIndex(
            namespace,
            persistedDeviceIndex(startDeviceIndex, run.nextDeviceIndex, retryableFailure = false),
        )
        val cycleNeedsAnotherPass = settings.cycleNeedsAnotherPass(namespace) || run.hasMoreAppendRows
        val cycleHadRejection = settings.cycleHadRejection(namespace) ||
            (run.rejectedBatches > 0 && !run.hasRetryableFailure)
        val cycleCompleted = run.nextDeviceIndex == 0
        settings.saveCycleNeedsAnotherPass(namespace, if (cycleCompleted) false else cycleNeedsAnotherPass)
        // If append pagination starts another cycle, carry any terminal rejection through that cycle;
        // otherwise a rejected table alongside a full append page could later be reported as success.
        settings.saveCycleHadRejection(
            namespace,
            if (cycleCompleted && !cycleNeedsAnotherPass) false else cycleHadRejection,
        )

        return when {
            !cycleCompleted || cycleNeedsAnotherPass -> {
                Execution.CONTINUE
            }
            cycleHadRejection -> {
                Execution.TERMINAL_FAILURE
            }
            else -> {
                Execution.COMPLETE
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

    private fun retryOrStop(message: String): Decision =
        if (!shouldRetryPush(runAttemptCount)) {
            Decision(
                Result.failure(), false, status = Status.FAILED,
                message = "Push paused after repeated failures; the next sync can try again.",
            )
        } else {
            Decision(Result.retry(), true, status = Status.RETRYING, message = message)
        }

    private fun recordSettledStatus(
        settings: SelfHostedPushSettings,
        decision: Decision,
        pending: Boolean,
    ) {
        when {
            pending && !decision.willRetry -> settings.recordContinuation()
            decision.status == Status.SUCCESS -> settings.recordSuccess()
            decision.status == Status.CONTINUING -> settings.recordContinuation()
            decision.status == Status.RETRYING -> settings.recordRetrying(decision.message.orEmpty())
            decision.status == Status.FAILED -> settings.recordError(decision.message.orEmpty())
            else -> Unit
        }
    }

    private companion object {
        const val MAX_DEVICES_PER_RUN = 1
    }
}

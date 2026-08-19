package com.noop.push

import kotlinx.coroutines.ExperimentalCoroutinesApi
import kotlinx.coroutines.suspendCancellableCoroutine
import okhttp3.Call
import okhttp3.Callback
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.RequestBody.Companion.toRequestBody
import okhttp3.Response
import java.io.IOException
import java.util.concurrent.TimeUnit
import kotlin.coroutines.resume
import kotlin.coroutines.resumeWithException

/** Minimal HTTP adapter: no redirects, no logging, and bounded acknowledgement/error reads. */
class PushHttpTransport(
    private val endpoint: PushEndpointPolicy.ValidEndpoint,
    private val bearerToken: String,
    private val client: OkHttpClient = defaultClient(),
) : PushTransport {
    override suspend fun post(batch: PushBatch): PushTransportResponse {
        val request = Request.Builder()
            .url(endpoint.url)
            .header("Authorization", "Bearer $bearerToken")
            .header("Accept", "application/json")
            .post(batch.body.toRequestBody(NDJSON))
            .build()
        return client.newCall(request).await().use { response ->
            val bytes = response.body?.byteStream()?.use { input ->
                val bounded = ByteArray(PushProtocol.MAX_ACK_BYTES + 1)
                var total = 0
                while (total < bounded.size) {
                    val read = input.read(bounded, total, bounded.size - total)
                    if (read < 0) break
                    total += read
                }
                bounded.copyOf(total)
            } ?: ByteArray(0)
            PushTransportResponse(response.code, bytes)
        }
    }

    /** Cancelling WorkManager cancels the active socket instead of waiting for the blocking timeout. */
    @OptIn(ExperimentalCoroutinesApi::class)
    private suspend fun Call.await(): Response = suspendCancellableCoroutine { continuation ->
        continuation.invokeOnCancellation { cancel() }
        enqueue(object : Callback {
            override fun onFailure(call: Call, e: IOException) {
                if (continuation.isActive) continuation.resumeWithException(e)
            }

            override fun onResponse(call: Call, response: Response) {
                if (continuation.isActive) {
                    continuation.resume(response) { response.close() }
                } else {
                    response.close()
                }
            }
        })
    }

    companion object {
        private val NDJSON = "application/x-ndjson; charset=utf-8".toMediaType()
        internal fun defaultClient(): OkHttpClient = OkHttpClient.Builder()
            .followRedirects(false)
            .followSslRedirects(false)
            .connectTimeout(10, TimeUnit.SECONDS)
            .readTimeout(15, TimeUnit.SECONDS)
            .writeTimeout(15, TimeUnit.SECONDS)
            .callTimeout(15, TimeUnit.SECONDS)
            .build()
    }
}

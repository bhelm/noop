package com.noop.push

import kotlinx.coroutines.runBlocking
import okhttp3.OkHttpClient
import okhttp3.Protocol
import okhttp3.Response
import okhttp3.ResponseBody.Companion.toResponseBody
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

class PushHttpTransportPolicyTest {
    @Test fun redirectsAreDisabledInBothDirections() {
        val client = PushHttpTransport.defaultClient()
        assertFalse(client.followRedirects)
        assertFalse(client.followSslRedirects)
    }

    @Test fun redirectIsReturnedWithoutFollowingAndAuthorizationIsHeaderOnly() = runBlocking {
        var calls = 0
        var authorization: String? = null
        var sentBody = ""
        val client = OkHttpClient.Builder().addInterceptor { chain ->
            calls++
            authorization = chain.request().header("Authorization")
            sentBody = chain.request().body!!.let { body ->
                val buffer = okio.Buffer()
                body.writeTo(buffer)
                buffer.readUtf8()
            }
            Response.Builder()
                .request(chain.request())
                .protocol(Protocol.HTTP_1_1)
                .code(307)
                .message("redirect")
                .header("Location", "https://elsewhere.example/steal")
                .body(ByteArray(0).toResponseBody())
                .build()
        }.build()
        val endpoint = (PushEndpointPolicy.validate("https://receiver.example/push") as PushEndpointPolicy.Result.Valid).endpoint
        val response = PushHttpTransport(endpoint, "top-secret", client).post(batch())

        assertEquals(307, response.statusCode)
        assertEquals(1, calls)
        assertEquals("Bearer top-secret", authorization)
        assertFalse(sentBody.contains("top-secret"))
    }

    @Test fun responseReadIsBoundedForAckAndErrorBodies() = runBlocking {
        val oversized = ByteArray(PushProtocol.MAX_ACK_BYTES + 500) { 'x'.code.toByte() }
        val client = OkHttpClient.Builder().addInterceptor { chain ->
            Response.Builder()
                .request(chain.request())
                .protocol(Protocol.HTTP_1_1)
                .code(500)
                .message("error")
                .body(oversized.toResponseBody())
                .build()
        }.build()
        val endpoint = (PushEndpointPolicy.validate("https://receiver.example/push") as PushEndpointPolicy.Result.Valid).endpoint
        val response = PushHttpTransport(endpoint, "secret", client).post(batch())

        assertEquals(PushProtocol.MAX_ACK_BYTES + 1, response.body.size)
        assertTrue(response.body.size < oversized.size)
    }

    private fun batch() = PushBatch(
        protocolVersion = PushProtocol.VERSION,
        batchId = "00000000-0000-4000-8000-000000000001",
        sourceId = "00000000-0000-4000-8000-000000000002",
        table = PushAppendTable.HR_SAMPLE,
        deviceId = "strap",
        mode = "append",
        startCursor = null,
        endCursor = null,
        recordCount = 0,
        window = null,
        body = "{}\n".toByteArray(),
    )
}

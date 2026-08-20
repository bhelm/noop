package com.noop.push

import kotlinx.coroutines.runBlocking
import okhttp3.OkHttpClient
import okhttp3.Protocol
import okhttp3.Response
import okhttp3.ResponseBody.Companion.toResponseBody
import okio.Buffer
import okio.GzipSource
import okio.buffer
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

class PushHttpTransportPolicyTest {
    @Test fun gzipEncodingIsDeterministicAndBounded() {
        val decoded = ByteArray(PushProtocol.MAX_BODY_BYTES) { index -> (index * 31).toByte() }

        val first = PushHttpTransport.gzip(decoded)
        val retry = PushHttpTransport.gzip(decoded)

        assertTrue(first.size <= PushProtocol.MAX_WIRE_BODY_BYTES)
        assertTrue(first.contentEquals(retry))
    }

    @Test fun redirectsAreDisabledInBothDirections() {
        val client = PushHttpTransport.defaultClient()
        assertFalse(client.followRedirects)
        assertFalse(client.followSslRedirects)
    }

    @Test fun definitiveUnsupportedMediaTypeRetriesSameEntityOnceAsIdentity() = runBlocking {
        val encodings = mutableListOf<String?>()
        val decodedBodies = mutableListOf<ByteArray>()
        val client = OkHttpClient.Builder().addInterceptor { chain ->
            val encoding = chain.request().header("Content-Encoding")
            encodings += encoding
            val wire = Buffer().also { chain.request().body!!.writeTo(it) }
            decodedBodies += if (encoding == "gzip") {
                GzipSource(wire).buffer().use { it.readByteArray() }
            } else {
                wire.readByteArray()
            }
            Response.Builder()
                .request(chain.request())
                .protocol(Protocol.HTTP_1_1)
                .code(if (encoding == "gzip") 415 else 200)
                .message("response")
                .body(ByteArray(0).toResponseBody())
                .build()
        }.build()
        val endpoint = (PushEndpointPolicy.validate("https://receiver.example/push") as PushEndpointPolicy.Result.Valid).endpoint

        val response = PushHttpTransport(endpoint, "secret", client).post(batch())

        assertEquals(200, response.statusCode)
        assertEquals(listOf("gzip", null), encodings)
        assertEquals(2, decodedBodies.size)
        assertTrue(decodedBodies[0].contentEquals(decodedBodies[1]))
    }

    @Test fun fallbackIsNotUsedForTransientErrors() = runBlocking {
        var calls = 0
        val client = OkHttpClient.Builder().addInterceptor { chain ->
            calls++
            Response.Builder()
                .request(chain.request())
                .protocol(Protocol.HTTP_1_1)
                .code(500)
                .message("error")
                .body(ByteArray(0).toResponseBody())
                .build()
        }.build()
        val endpoint = (PushEndpointPolicy.validate("https://receiver.example/push") as PushEndpointPolicy.Result.Valid).endpoint

        val response = PushHttpTransport(endpoint, "secret", client).post(batch())

        assertEquals(500, response.statusCode)
        assertEquals(1, calls)
    }

    @Test fun redirectIsReturnedWithoutFollowingAndAuthorizationIsHeaderOnly() = runBlocking {
        var calls = 0
        var authorization: String? = null
        var contentEncoding: String? = null
        var contentType: String? = null
        var contentLength: Long? = null
        var sentBody = ""
        val client = OkHttpClient.Builder().addInterceptor { chain ->
            calls++
            authorization = chain.request().header("Authorization")
            contentEncoding = chain.request().header("Content-Encoding")
            contentType = chain.request().body!!.contentType().toString()
            contentLength = chain.request().body!!.contentLength()
            sentBody = chain.request().body!!.let { body ->
                val buffer = Buffer()
                body.writeTo(buffer)
                GzipSource(buffer).buffer().use { it.readUtf8() }
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
        assertEquals("gzip", contentEncoding)
        assertEquals("application/x-ndjson; charset=utf-8", contentType)
        assertTrue(contentLength!! > 0L)
        assertEquals("{}\n", sentBody)
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

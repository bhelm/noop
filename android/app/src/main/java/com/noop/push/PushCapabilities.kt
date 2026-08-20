package com.noop.push

import org.json.JSONArray
import org.json.JSONObject

/** A receiver may only narrow the fixed protocol 1.0 registry, never name new data. */
data class PushCapabilities(
    val appendTables: Set<PushAppendTable>,
    val mutableTables: Set<PushMutableTable>,
) {
    val isEmpty: Boolean get() = appendTables.isEmpty() && mutableTables.isEmpty()
    val wireNames: List<String> get() =
        PushAppendTable.entries.filter { it in appendTables }.map { it.wireName } +
            PushMutableTable.entries.filter { it in mutableTables }.map { it.wireName }

    companion object {
        val ALL = PushCapabilities(PushAppendTable.entries.toSet(), PushMutableTable.entries.toSet())

        fun parse(bytes: ByteArray): PushCapabilities {
            if (bytes.size > PushProtocol.MAX_ACK_BYTES) {
                throw PushProtocolException("capabilities exceed size limit")
            }
            val obj = try {
                JSONObject(bytes.toString(Charsets.UTF_8))
            } catch (_: Throwable) {
                throw PushProtocolException("capabilities are not valid JSON")
            }
            if (obj.keys().asSequence().toSet() != setOf("type", "protocolVersion", "streams")) {
                throw PushProtocolException("capability members do not exactly match protocol 1.0")
            }
            if (obj.opt("type") != "capabilities" || obj.opt("protocolVersion") != PushProtocol.VERSION) {
                throw PushProtocolException("unsupported capability document")
            }
            val array = obj.opt("streams") as? JSONArray
                ?: throw PushProtocolException("capabilities.streams must be an array")
            val appendByName = PushAppendTable.entries.associateBy { it.wireName }
            val mutableByName = PushMutableTable.entries.associateBy { it.wireName }
            val seen = mutableSetOf<String>()
            val append = linkedSetOf<PushAppendTable>()
            val mutable = linkedSetOf<PushMutableTable>()
            for (index in 0 until array.length()) {
                val name = array.opt(index) as? String
                    ?: throw PushProtocolException("capability stream names must be strings")
                if (!seen.add(name)) throw PushProtocolException("duplicate capability stream")
                when (val table = appendByName[name] ?: mutableByName[name]) {
                    is PushAppendTable -> append += table
                    is PushMutableTable -> mutable += table
                    else -> throw PushProtocolException("unknown capability stream")
                }
            }
            return PushCapabilities(append, mutable)
        }
    }
}

internal class PushConnectionTester(
    private val transportFactory: (PushEndpointPolicy.ValidEndpoint, String) -> PushTransport =
        { endpoint, token -> PushHttpTransport(endpoint, token) },
) {
    suspend fun test(
        endpoint: PushEndpointPolicy.ValidEndpoint,
        token: String,
    ): PushCapabilitiesResult = try {
        transportFactory(endpoint, token).capabilities()
    } catch (cancelled: kotlinx.coroutines.CancellationException) {
        throw cancelled
    } catch (_: Throwable) {
        val failure = PushFailure(PushFailureCode.NETWORK_IO)
        PushCapabilitiesResult.Rejected(failure.safeCode, failure.retryable, failure)
    }
}

internal fun canStartPushConnectionTest(
    wifiAvailable: Boolean,
    endpointValid: Boolean,
    tokenAvailable: Boolean,
): Boolean = wifiAvailable && endpointValid && tokenAvailable

sealed interface PushCapabilitiesResult {
    data class Available(val capabilities: PushCapabilities) : PushCapabilitiesResult
    data class Rejected(
        val reason: String,
        val retryable: Boolean,
        val failure: PushFailure? = null,
    ) : PushCapabilitiesResult
}

package com.noop.push

import kotlinx.coroutines.runBlocking
import org.junit.Assert.assertEquals
import org.junit.Test

class PushWorkerGateTest {
    @Test fun staleDisabledWorkReturnsWithoutWifiTokenDatabaseOrNetwork() = runBlocking {
        var wifiCalls = 0
        var tokenCalls = 0
        var executeCalls = 0

        val outcome = PushWorkerGate.run(
            enabledEndpoint = { null },
            wifiAvailable = { wifiCalls++; true },
            token = { tokenCalls++; "secret" },
            execute = { _, _ -> executeCalls++; false },
        )

        assertEquals(PushWorkerGate.Outcome.DisabledOrInvalid, outcome)
        assertEquals(0, wifiCalls)
        assertEquals(0, tokenCalls)
        assertEquals(0, executeCalls)
    }

    @Test fun nonWifiStopsBeforeKeystoreDatabaseAndNetwork() = runBlocking {
        val endpoint = (PushEndpointPolicy.validate("https://example.com/") as PushEndpointPolicy.Result.Valid).endpoint
        var tokenCalls = 0
        var executeCalls = 0

        val outcome = PushWorkerGate.run(
            enabledEndpoint = { endpoint },
            wifiAvailable = { false },
            token = { tokenCalls++; "secret" },
            execute = { _, _ -> executeCalls++; false },
        )

        assertEquals(PushWorkerGate.Outcome.NotOnWifi, outcome)
        assertEquals(0, tokenCalls)
        assertEquals(0, executeCalls)
    }
}

package com.noop.push

import android.database.Cursor
import androidx.room.withTransaction
import androidx.sqlite.db.SimpleSQLiteQuery
import com.noop.data.WhoopDatabase

/**
 * Narrow, read-only Room snapshot adapter. SQL identifiers come exclusively from the closed enums below;
 * user/config input is always a bind argument. Every cursor is consumed and closed inside [withTransaction].
 */
class PushDao internal constructor(private val db: WhoopDatabase) : PushSnapshotSource {
    override suspend fun knownDeviceIds(): List<String> = db.withTransaction {
        val sql = buildString {
            append("SELECT id AS deviceId FROM device WHERE id <> ''")
            ALL_WIRE_TABLES.forEach { table ->
                append(" UNION ")
                append("SELECT deviceId FROM ${table.sqlName} WHERE deviceId <> ''")
            }
            append(" ORDER BY deviceId")
        }
        db.query(SimpleSQLiteQuery(sql)).use { cursor ->
            buildList {
                while (cursor.moveToNext()) cursor.getString(0)?.takeIf { it.isNotBlank() }?.let(::add)
            }
        }
    }

    override suspend fun appendRecordAt(
        table: PushAppendTable,
        deviceId: String,
        rowId: Long,
    ): PushAppendRecord? {
        val spec = appendSpec(table)
        return db.withTransaction {
            val sql = "SELECT rowid AS _pushRowId, ${spec.columns.joinToString()} FROM ${spec.sqlName} " +
                "WHERE deviceId = ? AND rowid = ? LIMIT 1"
            db.query(SimpleSQLiteQuery(sql, arrayOf(deviceId, rowId))).use { cursor ->
                if (cursor.moveToFirst()) cursor.appendRecord(spec) else null
            }
        }
    }

    override suspend fun appendRows(
        table: PushAppendTable,
        deviceId: String,
        afterRowId: Long,
        limit: Int,
    ): List<PushAppendRecord> {
        require(limit in 1..PushProtocol.MAX_RECORDS + 1)
        val spec = appendSpec(table)
        return db.withTransaction {
            val sql = "SELECT rowid AS _pushRowId, ${spec.columns.joinToString()} FROM ${spec.sqlName} " +
                "WHERE deviceId = ? AND rowid > ? ORDER BY rowid ASC LIMIT ?"
            db.query(SimpleSQLiteQuery(sql, arrayOf(deviceId, afterRowId, limit))).use { cursor ->
                buildList {
                    while (cursor.moveToNext()) add(cursor.appendRecord(spec))
                }
            }
        }
    }

    override suspend fun mutableRows(
        table: PushMutableTable,
        deviceId: String,
        window: PushWindow,
        limit: Int,
    ): List<PushMutableRecord> {
        require(limit in 1..(PushProtocol.MAX_MUTABLE_SNAPSHOT_RECORDS + 1))
        val spec = mutableSpec(table)
        return db.withTransaction {
            val (predicate, bounds) = when (table) {
                PushMutableTable.DAILY_METRIC, PushMutableTable.JOURNAL ->
                    "day >= ? AND day <= ?" to arrayOf<Any?>(window.fromDay, window.toDay)
                PushMutableTable.SLEEP_SESSION, PushMutableTable.WORKOUT ->
                    "startTs >= ? AND startTs < ?" to
                        arrayOf<Any?>(window.startTsInclusive, window.endTsExclusive)
            }
            val sql = "SELECT ${spec.columns.joinToString()} FROM ${spec.sqlName} " +
                "WHERE deviceId = ? AND $predicate ORDER BY ${spec.keyColumns.joinToString()} ASC LIMIT ?"
            val args = arrayOfNulls<Any?>(bounds.size + 2)
            args[0] = deviceId
            bounds.copyInto(args, destinationOffset = 1)
            args[args.lastIndex] = limit
            db.query(SimpleSQLiteQuery(sql, args)).use { cursor ->
                buildList {
                    while (cursor.moveToNext()) add(cursor.mutableRecord(spec))
                }
            }
        }
    }

    private fun Cursor.appendRecord(spec: TableSpec): PushAppendRecord {
        val values = values(spec)
        val rowId = getLong(getColumnIndexOrThrow("_pushRowId"))
        return PushAppendRecord(
            rowId,
            spec.keyColumns.associateWith(values::get),
            spec.dataColumns.associateWith(values::get),
        )
    }

    private fun Cursor.mutableRecord(spec: TableSpec): PushMutableRecord {
        val values = values(spec)
        return PushMutableRecord(
            spec.keyColumns.associateWith(values::get),
            spec.dataColumns.associateWith(values::get),
        )
    }

    private fun Cursor.values(spec: TableSpec): Map<String, Any?> = buildMap {
        for (name in spec.columns) {
            val index = getColumnIndexOrThrow(name)
            val value: Any? = when (getType(index)) {
                Cursor.FIELD_TYPE_NULL -> null
                Cursor.FIELD_TYPE_INTEGER -> if (name in spec.booleanColumns) getLong(index) != 0L else getLong(index)
                Cursor.FIELD_TYPE_FLOAT -> getDouble(index)
                Cursor.FIELD_TYPE_STRING -> getString(index)
                else -> throw PushProtocolException("unsupported SQLite type in ${spec.sqlName}.$name")
            }
            put(name, value)
        }
    }

    private data class TableSpec(
        val sqlName: String,
        val keyColumns: List<String>,
        val dataColumns: List<String>,
        val booleanColumns: Set<String> = emptySet(),
    ) {
        val columns: List<String> = keyColumns + dataColumns
    }

    private fun appendSpec(table: PushAppendTable): TableSpec = when (table) {
        PushAppendTable.HR_SAMPLE -> HR
        PushAppendTable.RR_INTERVAL -> RR
        PushAppendTable.EVENT -> EVENT
        PushAppendTable.BATTERY -> BATTERY
        PushAppendTable.SPO2_SAMPLE -> SPO2
        PushAppendTable.SKIN_TEMP_SAMPLE -> SKIN_TEMP
        PushAppendTable.RESP_SAMPLE -> RESP
        PushAppendTable.GRAVITY_SAMPLE -> GRAVITY
    }

    private fun mutableSpec(table: PushMutableTable): TableSpec = when (table) {
        PushMutableTable.DAILY_METRIC -> DAILY
        PushMutableTable.SLEEP_SESSION -> SLEEP
        PushMutableTable.WORKOUT -> WORKOUT
        PushMutableTable.JOURNAL -> JOURNAL
    }

    private companion object {
        val HR = TableSpec("hrSample", listOf("ts"), listOf("bpm"))
        val RR = TableSpec(
            "rrInterval",
            listOf("ts", "rrMs", "seq"),
            listOf("ord", "srcChannel", "tsSuspect"),
        )
        val EVENT = TableSpec(
            "event", listOf("ts", "kind"), listOf("payloadJSON"),
        )
        val BATTERY = TableSpec(
            "battery", listOf("ts"), listOf("soc", "mv", "charging"),
            booleanColumns = setOf("charging"),
        )
        val SPO2 = TableSpec(
            "spo2Sample", listOf("ts"), listOf("red", "ir"),
        )
        val SKIN_TEMP = TableSpec(
            "skinTempSample", listOf("ts"), listOf("raw", "aux1Raw", "aux2Raw"),
        )
        val RESP = TableSpec("respSample", listOf("ts"), listOf("raw"))
        val GRAVITY = TableSpec(
            "gravitySample", listOf("ts"), listOf("x", "y", "z", "dynAccel"),
        )
        val DAILY = TableSpec(
            "dailyMetric",
            listOf("day"),
            listOf(
                "totalSleepMin", "efficiency", "deepMin", "remMin", "lightMin",
                "disturbances", "restingHr", "avgHrv", "recovery", "strain", "exerciseCount", "spo2Pct",
                "skinTempDevC", "respRateBpm", "steps", "activeKcalEst", "spo2Red", "spo2Ir",
            ),
        )
        val SLEEP = TableSpec(
            "sleepSession",
            listOf("startTs"),
            listOf(
                "endTs", "efficiency", "restingHr", "avgHrv", "stagesJSON",
                "userEdited", "startTsAdjusted", "motionJSON", "sleepStateJSON", "stagingSparse",
            ),
            booleanColumns = setOf("userEdited", "stagingSparse"),
        )
        val WORKOUT = TableSpec(
            "workout",
            listOf("startTs", "sport"),
            listOf(
                "endTs", "source", "durationS", "energyKcal", "avgHr",
                "maxHr", "strain", "distanceM", "zonesJSON", "notes", "routePolyline", "steps",
            ),
        )
        val JOURNAL = TableSpec(
            "journal", listOf("day", "question"), listOf("answeredYes", "notes", "numericValue"),
            booleanColumns = setOf("answeredYes"),
        )
        val ALL_WIRE_TABLES = listOf(HR, RR, EVENT, BATTERY, SPO2, SKIN_TEMP, RESP, GRAVITY, DAILY, SLEEP, WORKOUT, JOURNAL)
    }
}

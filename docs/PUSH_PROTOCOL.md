# Self-hosted push protocol

This document specifies the wire contract for NOOP's **Experimental**, default-off export to a
user-owned HTTP(S) endpoint. Protocol version **1.0** covers the Android-first client. It is a
one-way export protocol: the on-device database is authoritative, the receiver only acknowledges
writes, and NOOP never reads health data or configuration back from the receiver.

NOOP does not ship, operate, or endorse a receiver. A receiver is not part of this repository, and
this contract must not be interpreted as an account, hosted-sync, restore, or two-way-sync API.

## Transport and authentication

The configured endpoint accepts one `POST` per batch.

```http
POST /the/user-configured-path HTTP/1.1
Content-Type: application/x-ndjson; charset=utf-8
Accept: application/json
Authorization: Bearer <user-supplied-token>
```

- HTTPS endpoints may be public. Plain HTTP is accepted only when the URL uses a numeric loopback,
  RFC 1918 private IPv4, IPv4/IPv6 link-local, or IPv6 ULA address. Cleartext hostnames (including
  `.local`) and public cleartext destinations are rejected, eliminating a DNS-rebinding boundary. A
  bearer token sent over allowed local HTTP is still visible locally, so HTTPS remains preferable.
- The token and endpoint are supplied by the user. Neither identifies a NOOP account; no such account
  exists.
- One request contains exactly one device and one stream. A v1 request contains at most **500 record
  lines** and at most **1 MiB (1,048,576 bytes)** of UTF-8 NDJSON, including newlines.
- A sender run also caps one mutable-window snapshot at **1,000 records / 2 MiB encoded record
  data**. Larger local windows fail visibly before the first HTTP request instead of growing memory
  without bound or sending an incomplete authoritative replacement.
- Network or receiver failure must not block strap offload, local writes, analytics, or UI. Delivery
  is retried by the independent background worker.
- Android coalesces triggers that arrive during a running worker, processes at most one remembered
  device scope per attempt, and rotates that durable device cursor only after the slice completes.
  Per-call DNS and HTTP deadlines keep the attempt below WorkManager's execution window; automatic
  retries stop after a finite attempt budget and resume on a later successful offload or app launch.

## Identity and storage scope

`sourceId` is a random UUID generated locally and persisted for that app installation. `deviceId` is
the identifier already used by NOOP's local `device` table. Neither is an account or a globally
resolved user identity. The device name and MAC address are not part of this protocol.

A receiver must scope every row and idempotency record by at least:

```text
(sourceId, deviceId, stream, primary key)
```

Two installations that happen to use the same strap identifier must therefore not overwrite one
another. Reinstalling NOOP may create a new `sourceId`; reconciliation between installations is
deliberately outside v1.

## NDJSON request

Every line is one complete JSON object followed by LF (`0x0a`). There is no BOM, blank line, JSON
array, or trailing material. The first line is the batch header; exactly `recordCount` record lines
follow it.

### Header

An append batch has this shape (spacing is illustrative, not canonical):

```json
{"type":"batch","protocolVersion":"1.0","batchId":"e835f32f-60e7-4c93-90a0-51eb6830119a","sourceId":"3a3486dd-5030-4e17-a00d-a781399890f9","deviceId":"strap-local-id","stream":"hrSample","delivery":"append","recordCount":2,"startCursor":{"rowId":48110,"keySha256":"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"},"endCursor":{"rowId":48119,"keySha256":"bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"}}
{"type":"record","key":{"ts":1723939201},"data":{"bpm":61}}
{"type":"record","key":{"ts":1723939202},"data":{"bpm":62}}
```

Header members are:

| Member | Meaning |
|---|---|
| `type` | Always `"batch"`. |
| `protocolVersion` | `"1.0"` for this contract. |
| `batchId` | UUID identifying these exact request bytes. Stable across retries. |
| `sourceId` | Locally generated installation UUID. |
| `deviceId` | Local device identifier; scopes every record in the batch. |
| `stream` | A name in the versioned stream registry below. |
| `delivery` | `"append"` or `"replace_window"`, as fixed by the registry. |
| `recordCount` | Number of record lines, `0...500`. |
| `startCursor` | Exclusive append insertion highwater, or `null` for the first append batch and replace-window parts. |
| `endCursor` | Inclusive insertion highwater of the final append record, or `null` for replace-window parts. |
| `window` | Required only for `replace_window` delivery; absent for append delivery. |

All UUIDs are lowercase canonical UUID strings. JSON object member order is not semantically
significant, but the sender must retain the exact encoded bytes until that batch is acknowledged.

### Records

Each subsequent object has `type: "record"`, a `key` containing every primary-key column other than
the header's `deviceId`, and a `data` object containing the exported non-key columns. Local-only
bookkeeping such as the vestigial `synced` column is never exported. SQL `NULL` is JSON `null`, integer
and real values are JSON numbers, text is a JSON string, and database booleans are JSON booleans.
Text columns whose names end in `JSON` remain strings; their contents are not promoted into nested
wire objects.

Keys must contain exactly the registry columns. Append records are ordered by their local insertion
position; replace-window records are sorted by natural key. Natural-key integer components compare
numerically and text components use unsigned UTF-8 byte order (SQLite `BINARY` order). A receiver
must reject duplicate keys within a batch or complete replacement window.

## Append delivery and cursors

Each append highwater is local state scoped by `(sourceId, deviceId, stream)`. It is an opaque wire
object backed by the SQLite insertion `rowid`, not a measurement timestamp or natural primary key.
The sender selects rows for that device whose `rowid` is greater than `startCursor.rowId`, orders by
`rowid ASC`, and sets `endCursor.rowId` to the final row's insertion position. This matters because a
later offload can insert old measurement timestamps; a timestamp or lexicographic natural-key
highwater would permanently strand that backfill.

Each non-null cursor also contains `keySha256`, the lowercase SHA-256 of the UTF-8 bytes formed by
`stream`, LF, `deviceId`, LF, and the compact JSON natural-key object with members in registry order.
Before using a saved cursor, the sender must read
that row and verify the natural-key fingerprint. A missing row or mismatch means a restore, prune,
`VACUUM`/rowid rewrite, or database replacement invalidated the insertion positions. The sender must
reset that stream's cursor to null and replay it; receiver primary-key upserts make the replay safe.
Changing `sourceId` or the normalized endpoint selects a fresh progress namespace and sends a full
baseline, avoiding acknowledgements being carried between destinations. Rotating the bearer token
for the same normalized endpoint preserves progress. Implementations may store additional local
database-generation evidence, but it is not transmitted.

The sender also remembers, per endpoint namespace, every device scope it has considered. Live
database discovery is unioned with that encrypted set so deleting the final mutable row still emits
an empty authoritative replacement instead of leaving stale receiver data behind.

An append batch must contain at least one record. Its keys must satisfy:

```text
startCursor.rowId < first rowid < ... <= final rowid == endCursor.rowId
```

For an initial batch, `startCursor` is `null` and all insertion positions are eligible. Batches stop before either
the 500-record or 1-MiB bound is exceeded. A receiver applies records as idempotent upserts using the
scoped primary key. Append streams do not communicate deletions.

The sender advances a stream's local highwater to `endCursor` only after a valid acceptance response
for the whole batch. A timeout, non-2xx response, invalid body, partial acceptance, or mismatched
acknowledgement leaves the highwater unchanged and retries the identical `batchId` and bytes. Other
streams have independent highwaters and may continue.

## Authoritative rolling-window delivery

Mutable and recomputed tables use authoritative `replace_window` operations rather than append cursors. After
an offload, the sender should cover the current local calendar day plus the preceding 13 local days
(approximately 14 times 24 hours across daylight-saving changes). Day-keyed windows use `YYYY-MM-DD`;
timestamp-keyed windows use the corresponding local-midnight bounds converted to Unix seconds.
Bounds are always half-open: `startInclusive <= selector < endExclusive`.

The window header member is:

```json
"window":{"replacementId":"bf8b735e-f157-4a35-beb2-9b086d10d5bd","selector":"day","startInclusive":"2026-08-05","endExclusive":"2026-08-19","part":1,"parts":1}
```

For `startTs` selectors the bounds are integer Unix seconds instead of strings. A replacement that fits
in one request uses `part: 1, parts: 1`. If it exceeds either batch bound, the sender divides the
sorted records into `parts` bounded requests. Every part has the same `replacementId`, scope, window and
positive `parts` value; `part` runs from 1 through `parts`; each part has its own stable `batchId`.
An empty window is represented by one zero-record part and is still authoritative: it deletes all
receiver rows in that scope and window. `startCursor` and `endCursor` are `null` for all parts.

The receiver durably stages accepted parts. Only when every part is present does it atomically:

1. upsert all replacement records by the scoped primary key; and
2. delete receiver rows in the declared window whose keys are absent from the complete replacement.

An acknowledgement for the part that completes the set must not be returned until that atomic apply
succeeds. Retrying any part is harmless. Conflicting reuse of a `replacementId`, part number, or
`batchId` must be rejected. Rows outside the declared window are untouched. This absence-means-delete
rule makes edits and deletions within the rolling window converge to NOOP's local state; v1 carries no
tombstone for a row that has already aged out of that window.

## Version 1 stream registry

The v1 registry is deliberately finite. A table present in NOOP's database is **not** implicitly part
of the protocol.

### Append streams

| `stream` | Natural key | `data` members |
|---|---|---|
| `hrSample` | `ts` | `bpm` |
| `rrInterval` | `ts`, `rrMs`, `seq` | `ord` (nullable), `srcChannel` (nullable), `tsSuspect` (nullable) |
| `event` | `ts`, `kind` | `payloadJSON` |
| `battery` | `ts` | `soc` (nullable), `mv` (nullable), `charging` (nullable) |
| `spo2Sample` | `ts` | `red`, `ir` |
| `skinTempSample` | `ts` | `raw`, `aux1Raw` (nullable), `aux2Raw` (nullable) |
| `respSample` | `ts` | `raw` |
| `gravitySample` | `ts` | `x`, `y`, `z`, `dynAccel` (nullable) |

`ts`, `rrMs`, `seq`, `bpm`, `red`, `ir`, and `raw` are integers. `soc`, `x`, `y`, `z`, and
`dynAccel` are finite numbers. The nullable RR metadata is integer-valued. `charging` is boolean or
null. The database's `deviceId` is supplied by the header and `synced` is intentionally omitted.

### Mutable replace-window streams

| `stream` | Key | Window selector | `data` members |
|---|---|---|---|
| `dailyMetric` | `day` | `day` | `totalSleepMin`, `efficiency`, `deepMin`, `remMin`, `lightMin`, `disturbances`, `restingHr`, `avgHrv`, `recovery`, `strain`, `exerciseCount`, `spo2Pct`, `skinTempDevC`, `respRateBpm`, `steps`, `activeKcalEst`, `spo2Red`, `spo2Ir` (all nullable) |
| `sleepSession` | `startTs` | `startTs` | `endTs`, `efficiency`, `restingHr`, `avgHrv`, `stagesJSON`, `userEdited`, `startTsAdjusted`, `motionJSON`, `sleepStateJSON`, `stagingSparse` |
| `workout` | `startTs`, `sport` | `startTs` | `endTs`, `source`, `durationS`, `energyKcal`, `avgHr`, `maxHr`, `strain`, `distanceM`, `zonesJSON`, `notes`, `routePolyline`, `steps` |
| `journal` | `day`, `question` | `day` | `answeredYes`, `notes`, `numericValue` |

Unless inherent above, mutable data members are nullable exactly as in the current Room schema.
`sleepSession.endTs`, `workout.endTs`, `workout.source`, and `journal.answeredYes` are required;
`sleepSession.userEdited` is a required boolean. `day` is `YYYY-MM-DD`; timestamp and count fields
are integers; metric and measurement fields are finite numbers. See [DATA_MODEL.md](DATA_MODEL.md)
and `android/app/src/main/java/com/noop/data/Entities.kt` for the local meanings and units. The wire
registry, not automatic reflection over either database, determines what is sent.

Newer tables such as `ppgHrSample`, `stepSample`, `sleepStateSample`, `metricSeries`, raw waveform /
IMU tables, and any future schema additions are not silently exported by v1. Adding a stream or an
optional `data` member requires a documented registry update and protocol minor version.

## Acceptance, errors, and retry idempotency

After atomically accepting an append batch or durably accepting a replace-window part, the receiver returns
2xx with `Content-Type: application/json` and exactly one acknowledgement object:

```json
{"protocolVersion":"1.0","batchId":"e835f32f-60e7-4c93-90a0-51eb6830119a","stream":"hrSample","deviceId":"strap-local-id","endCursor":{"rowId":48119,"keySha256":"bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"},"acceptedRows":2,"status":"accepted"}
```

Acceptance is valid only if all of these exactly match the request:

- `protocolVersion`
- `batchId`
- `stream`
- `deviceId`
- `endCursor` (including `null` for replace-window parts)
- `acceptedRows == recordCount`
- `status == "accepted"`

Any missing or mismatched member is a failed delivery even when the HTTP status is 2xx. There is no
partial success. The response contains acknowledgement metadata only; it must not contain source
records, remote changes, commands, cursors chosen by the server, or configuration for NOOP to apply.

A receiver must remember the hash and acceptance result of each batch under
`(sourceId, deviceId, batchId)`. Repeating the same `batchId` with byte-identical content returns the
same acknowledgement without duplicating effects. Reusing it with different bytes is a conflict and
must not modify data. Recommended failures are `400` for malformed NDJSON, `401`/`403` for auth,
`409` for conflicting identifiers or replace-window parts, `413` for a body over 1 MiB, `422` for an
unsupported protocol/stream or invalid record, and `5xx` for a transient receiver failure. NOOP
automatically retries transport errors, `408`, `429`, and `5xx`. Other `4xx` responses and malformed
or mismatched acknowledgements retain progress and surface a visible configuration/protocol error;
they are retried only after a later trigger or configuration change. Responses are never consumed as
health data.

## Versioning and forward compatibility

`protocolVersion` is `MAJOR.MINOR`:

- A major version changes framing, required members, keys, or existing semantics. A receiver must
  reject an unsupported major version.
- A minor version may add an optional header/data member or a registry stream. Receivers supporting
  the same major must ignore unknown object members. They may reject an unknown stream without
  rejecting batches for supported streams.
- A sender must not emit a new stream or field while claiming an older minor version. Removing or
  changing the meaning/type of a field, or changing a stream key or delivery mode, requires a new
  major version.
- Receivers must reject malformed known members rather than guessing. They should preserve unknown
  `data` members if their storage model permits, but must not assign semantics to them.

The sender never negotiates by reading capabilities from the endpoint. Configuration chooses one
protocol version, and normal POST acknowledgements are the only server-to-client messages.

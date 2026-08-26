# 5/MG raw data capture

**Status:** experimental, user-initiated, hardware-verified on WHOOP 5/MG. The capture path is a
research/export facility; it does not feed recovery, sleep, steps, or another production score.

## What it is for

The Raw Data Collector records a bounded interval of the strap's high-rate six-axis motion data and
exports it with enough provenance to analyse later. Typical uses are protocol validation, labelled
activity datasets, offline algorithm development, and checking whether a Bluetooth interruption was
repaired by a later history sync.

It lives in **Test Centre → 5/MG Raw Data Collector** on Android and Apple platforms. A session may be
started and stopped live, or created afterwards for an existing time range. Start/end times of a
completed session can be edited, individual sessions can be deleted, and all completed sessions can
be deleted after confirmation. The generic collector does not assume a particular labelling task;
manual step/stair controls are optional fork extensions rather than a prerequisite for capture.

## What was verified on hardware

On WHOOP 5/MG, command 106 by itself can return `SUCCESS` without starting any IMU producer. The
working bounded sequence is:

1. `START_RAW_DATA` (81), payload `[0x01]`;
2. `TOGGLE_IMU_MODE` (106), payload `[0x01, 0x01]`;
3. receive and decode the 100 Hz six-axis buffers;
4. on stop, send `STOP_RAW_DATA` (82), payload `[0x01]`, then `TOGGLE_IMU_MODE` with
   `[0x01, 0x00]`.

The writes use the authenticated WHOOP command characteristic and require a connected/bonded strap.
An accepted command is not evidence that samples arrived, so the collector reports connection state,
request state, packet/byte counts, the last packet time, and history-sync progress separately.

The decoded IMU buffer contains 100 signed 16-bit samples for each of `ax, ay, az, gx, gy, gz`, keyed
by a strap Unix timestamp. Accelerometer scale is `1/4096 g/LSB`; gyroscope scale is
`0.06104 deg/s/LSB`. See [BLE reverse engineering](BLE_REVERSE_ENGINEERING.md#4-the-realtime-r10r11-raw-stream-type-43)
for the byte layout and validation evidence.

An Android Bluetooth HCI capture of the official WHOOP app around a Strength Trainer session was the
useful behavioural reference: high-rate collection was session-oriented rather than a permanently
enabled background stream. It helped identify what to test, but NOOP's implementation and validation
remain clean-room and use only independently implemented protocol commands/decoders. The observation
also does not establish the battery cost of keeping the mode enabled beyond a workout-sized window.

## Live capture and history repair

A live BLE connection is not assumed to be lossless. Each session is a time window tied to one strap,
and incoming IMU buffers are routed by **strap timestamp**, not by arrival time. If Bluetooth is off or
the phone is disconnected during part of a recording, matching delayed buffers from a later historical
offload can still be appended to the session. Duplicate timestamps are discarded.

Consequences for consumers:

- file order is not chronological: repaired older history may be appended after newer live data;
- strap timestamp is authoritative and readers must sort by it;
- export metadata reports actual chunk coverage and `imu_100hz_complete`; it must not infer complete
  capture merely because the user started and stopped a session;
- history can repair only data the strap actually retained. The design does not promise that every
  firmware retains every high-rate buffer for later offload.

The historical-range action is therefore useful even when the collector was not running at the time:
it creates a session window over raw IMU buffers already available locally or delivered by the next
history sync. A range is currently bounded to seven days to keep an accidental export finite.

## Storage design

High-rate samples do **not** live as one SQLite row per sample. That would add avoidable write
amplification, database growth, migration burden, and backup cost to data whose main consumer is a
sequential signal-analysis job.

Instead, storage is split by responsibility:

| Data | Storage | Lifetime |
|---|---|---|
| Session window, comments, optional labels | Small app-private JSON/JSONL metadata | Until the user deletes the session |
| Incoming raw IMU frames | Append-only `.imus` session file | Until materialised/exported or the session is deleted |
| Searchable chunk metadata | SQLite (`imuChunk`) | Index/provenance only; no individual samples |
| Immutable analysis/export payload | Compressed `.imuc` file | Pinned to the retained session/export |

The `.imus` writer batches up to 30 one-second frames into an independently zlib-compressed block.
Blocks are appended; a crash can at worst leave a truncated final block, while earlier blocks remain
readable. Pending blocks are flushed when a session stops or before it is read. Each record keeps the
strap timestamp, phone receive time, and original frame bytes. This preserves evidence for a future
decoder instead of committing early to today's interpretation.

An `.imuc` file is a ZIP/deflate container with:

- `manifest.json`: `NOOPIMU`, format version, 100 Hz sample rate, axes, timestamp source, coverage,
  ordering contract, and row count;
- `samples.bin`: repeated big-endian timestamp and byte-length headers followed by column-major,
  little-endian signed 16-bit axis samples.

The format and archive semantics are mirrored on Android and Apple. SHA-256, byte size, codec,
coverage, and sample count are stored in `imuChunk`; the payload itself remains outside SQLite.

## Session export

The shareable ZIP contains session provenance and all available sensor material for its selected
interval. Android currently includes `meta.json`, label/event JSONL and CSV files, decoded one-second
signals, raw sensor CSV, and `imu/*.imuc`. Apple exports equivalent session metadata/events,
`history-sensors.csv`, `imu-coverage.json`, and IMU chunks through its platform export path. Inspect
`meta.json` plus the platform's IMU coverage object first:

- `started_at_ms` / `ended_at_ms` are the selected analysis interval;
- `captured_started_at_ms` / `captured_ended_at_ms`, when present, preserve the physical recording
  interval even after the selected interval is edited;
- Android's `imu_100hz_coverage` identifies the chunks actually present and `imu_100hz_complete` is
  the conservative coverage result; Apple carries the same facts in `imu-coverage.json`;
- manual labels, when present, are annotations and never proof of sensor completeness.

Exports stay local until the user invokes the operating system's share sheet. Raw captures are not
part of routine cloud sync or telemetry, consistent with NOOP's offline-first privacy model.

## Scope and operational limits

- Capture is deliberately bounded and explicit. The current evidence does **not** establish battery,
  flash-retention, thermal, or BLE-airtime costs for continuous 24/7 100 Hz operation.
- A one-hour workout/research capture succeeding does not establish that a 36-hour rolling recorder is
  safe. Any future rolling buffer needs hardware measurements and an explicit retention policy.
- The older passive “Record 5/MG raw capture” protocol trace and the session collector have different
  jobs. The former preserves broad offload/debug frames; the latter owns a time-bounded, exportable IMU
  dataset. They may observe the same wire frame, but the session store deduplicates it by strap time.
- The former `rawImuSample` per-sample SQLite design was intentionally removed: no production path had
  written it, and high-rate payloads now use file-backed chunks.
- Do not use arrival order as time, do not fill gaps silently, and do not claim 100 Hz coverage from
  packet count alone.

## Implementation map

| Concern | Apple | Android |
|---|---|---|
| Collector UI | `Strand/Screens/RawDataCollectorView.swift` | `com.noop.ui.GroundTruthCollectorScreen` |
| Session metadata | `RawDataSessionStore` | `GroundTruthCollector` |
| Live command path | `BLEManager.startGroundTruthRawCapture` | `WhoopBleClient.startGroundTruthImuCapture` |
| Append/recovery window | `ImuSessionFileStore` | `ImuSessionFileStore` |
| Immutable chunk export | `ImuChunkArchiveStore` | `ImuChunkStore` |
| Raw decoder | `Whoop5RawImu` in `WhoopProtocol` | `Whoop5RawImu` in `com.noop.protocol` |

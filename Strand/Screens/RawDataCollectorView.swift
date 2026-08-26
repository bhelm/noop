import SwiftUI
import StrandDesign

/// iOS/macOS parity twin of Android's 5/MG Raw Data Collector screen.
struct RawDataCollectorView: View {
    @EnvironmentObject private var model: AppModel
    @EnvironmentObject private var live: LiveState
    @StateObject private var store = RawDataSessionStore()
    private let archive = ImuChunkArchiveStore()

    @State private var excludeMinutes = "5"
    @State private var exportingId: String?
    @State private var deleteCandidate: RawDataSessionStore.Session?
    @State private var confirmDeleteAll = false
    @State private var exportError: String?
    @State private var rawBatchCounts: [String: Int] = [:]
    @State private var historicalFrom = Date().addingTimeInterval(-3_600)
    @State private var historicalTo = Date()

    var body: some View {
        ScreenScaffold(
            title: "5/MG Raw Data Collector",
            subtitle: "Record a bounded 100 Hz motion session and export its complete timeline."
        ) {
            VStack(alignment: .leading, spacing: NoopMetrics.sectionSpacing) {
                countersCard
                coverageCard
                labelsCard
                controls
                historicalRangeCard
                sessionsSection
            }
        }
        .task {
            // Restore a session after navigation/process lifecycle changes. The BLE layer rejects a
            // duplicate arm, so this is safe when capture never stopped.
            if store.active != nil, live.bonded { model.ble.startGroundTruthRawCapture() }
            await refreshRawCounts()
        }
        .onChangeCompat(of: live.bonded) { bonded in
            if bonded, store.active != nil { model.ble.startGroundTruthRawCapture() }
        }
        .confirmationDialog("Delete this session?", isPresented: Binding(
            get: { deleteCandidate != nil }, set: { if !$0 { deleteCandidate = nil } }
        ), titleVisibility: .visible) {
            Button("Delete", role: .destructive) {
                guard let session = deleteCandidate else { return }
                deleteCandidate = nil
                Task { await delete(session) }
            }
            Button("Cancel", role: .cancel) { deleteCandidate = nil }
        } message: {
            if let session = deleteCandidate {
                Text("The session \(Self.range(session)) and its captured raw data will be deleted permanently.")
            }
        }
        .confirmationDialog("Delete all sessions?", isPresented: $confirmDeleteAll,
                            titleVisibility: .visible) {
            Button("Delete all", role: .destructive) { Task { await deleteAll() } }
            Button("Cancel", role: .cancel) { }
        } message: {
            Text("All \(store.sessions.count) recorded sessions and their captured raw data will be deleted permanently.")
        }
        .alert("Couldn't export session", isPresented: Binding(
            get: { exportError != nil }, set: { if !$0 { exportError = nil } }
        )) {
            Button("OK", role: .cancel) { exportError = nil }
        } message: { Text(exportError ?? "Unknown error") }
    }

    private var countersCard: some View {
        StrandCard {
            HStack {
                counter("NOOP steps", model.repo.days.last?.steps.map(String.init) ?? "–")
                counter("Manual steps", String(store.active?.steps ?? 0))
                counter("Stairs", String(store.active?.stairs ?? 0))
            }
        }
    }

    private var coverageCard: some View {
        StrandCard {
            VStack(alignment: .leading, spacing: NoopMetrics.space2) {
                Text("Capture coverage").font(StrandFont.headline).foregroundStyle(StrandPalette.textPrimary)
                Text(live.connected ? "Band: connected\(live.bonded ? " + paired" : "; pairing")"
                                    : "Band: disconnected")
                    .foregroundStyle(live.connected ? StrandPalette.statusPositive : StrandPalette.statusCritical)
                Text(live.backfilling
                     ? "History sync: running (\(live.syncChunksThisSession) chunks)"
                     : "History sync: idle")
                    .foregroundStyle(StrandPalette.textSecondary)
                if let active = store.active {
                    Text("Realtime IMU: session active since \(Self.time(active.startedAtMs))")
                        .foregroundStyle(StrandPalette.accent)
                }
            }
            .font(StrandFont.subhead)
        }
    }

    private var labelsCard: some View {
        StrandCard {
            VStack(alignment: .leading, spacing: NoopMetrics.space3) {
                Text("Optional manual labels").font(StrandFont.headline)
                    .foregroundStyle(StrandPalette.textPrimary)
                Text("For step-algorithm research, label one step or one stair plus one step. Raw capture works without labels.")
                    .font(StrandFont.caption).foregroundStyle(StrandPalette.textTertiary)
                HStack {
                    NoopButton("Step", systemImage: "figure.walk", kind: .secondary, fullWidth: true) {
                        store.record(.step)
                    }.disabled(store.active == nil)
                    NoopButton("Stair", systemImage: "stairs", kind: .secondary, fullWidth: true) {
                        store.record(.stair)
                    }.disabled(store.active == nil)
                }
            }
        }
    }

    @ViewBuilder private var controls: some View {
        if store.active != nil {
            HStack {
                NoopButton("Undo last click", systemImage: "arrow.uturn.backward", kind: .secondary,
                           fullWidth: true) { store.undo() }
                NoopButton("Stop session", systemImage: "stop.fill", kind: .destructive,
                           fullWidth: true) { Task { await stop() } }
            }
            HStack {
                TextField("Minutes", text: $excludeMinutes)
                    .textFieldStyle(.roundedBorder)
                NoopButton("Ignore recent period", kind: .secondary, fullWidth: true) {
                    if let minutes = Int(excludeMinutes) { store.excludeLast(minutes: minutes) }
                }
                .disabled(!(1...240).contains(Int(excludeMinutes) ?? 0))
            }
        } else {
            NoopButton("Start raw-data session", systemImage: "record.circle", kind: .primary,
                       fullWidth: true) { start() }
                .disabled(!live.bonded)
        }
    }

    private var historicalRangeCard: some View {
        StrandCard {
            VStack(alignment: .leading, spacing: NoopMetrics.space3) {
                Text("Historical export window").font(StrandFont.headline)
                Text("Create a session from synchronized history without starting a live capture. 100 Hz coverage is included wherever it still exists in the rolling buffer.")
                    .font(StrandFont.caption).foregroundStyle(StrandPalette.textTertiary)
                DatePicker("From", selection: $historicalFrom)
                DatePicker("To", selection: $historicalTo, in: historicalFrom...)
                NoopButton("Add historical session", systemImage: "clock.arrow.circlepath",
                           kind: .secondary, fullWidth: true) {
                    _ = store.createHistorical(deviceId: model.ble.deviceId,
                                               from: historicalFrom, to: historicalTo)
                }
                .disabled(historicalTo <= historicalFrom || historicalTo.timeIntervalSince(historicalFrom) > 7 * 86_400)
            }
        }
    }

    private var sessionsSection: some View {
        VStack(alignment: .leading, spacing: NoopMetrics.space3) {
            Text("Recorded sessions").font(StrandFont.title2).foregroundStyle(StrandPalette.textPrimary)
            if store.sessions.isEmpty {
                Text("No sessions recorded yet.").font(StrandFont.subhead)
                    .foregroundStyle(StrandPalette.textSecondary)
            } else {
                NoopButton("Delete all sessions", systemImage: "trash", kind: .destructive,
                           fullWidth: true) { confirmDeleteAll = true }
                    .disabled(store.active != nil)
                ForEach(store.sessions) { session in sessionCard(session) }
            }
        }
    }

    private func sessionCard(_ session: RawDataSessionStore.Session) -> some View {
        StrandCard {
            VStack(alignment: .leading, spacing: NoopMetrics.space3) {
                Text(Self.range(session)).font(StrandFont.headline).foregroundStyle(StrandPalette.textPrimary)
                if !session.active, let endMs = session.endedAtMs {
                    DatePicker("From", selection: Binding(
                        get: { Date(timeIntervalSince1970: Double(session.startedAtMs) / 1_000) },
                        set: { store.setRange(sessionId: session.id, from: $0,
                                              to: Date(timeIntervalSince1970: Double(endMs) / 1_000)) }
                    ))
                    DatePicker("To", selection: Binding(
                        get: { Date(timeIntervalSince1970: Double(endMs) / 1_000) },
                        set: { store.setRange(sessionId: session.id,
                                              from: Date(timeIntervalSince1970: Double(session.startedAtMs) / 1_000), to: $0) }
                    ))
                }
                Text("\(session.steps) steps · \(session.stairs) stairs · \(session.excludedWindows) excluded periods")
                    .font(StrandFont.caption).foregroundStyle(StrandPalette.textSecondary)
                Text(session.active ? "Export status: recording"
                     : "Export status: \(rawBatchCounts[session.id, default: 0]) raw batches")
                    .font(StrandFont.caption)
                    .foregroundStyle(session.active ? StrandPalette.statusWarning : StrandPalette.statusPositive)
                TextField("Session comment", text: Binding(
                    get: { store.sessions.first(where: { $0.id == session.id })?.comment ?? session.comment },
                    set: { store.setComment($0, sessionId: session.id) }
                ), axis: .vertical)
                    .textFieldStyle(.roundedBorder).lineLimit(2...4)
                NoopButton(exportingId == session.id ? "Building export…" : "Export session",
                           systemImage: "square.and.arrow.up", kind: .secondary, fullWidth: true) {
                    Task { await export(session) }
                }
                .disabled(session.active || exportingId != nil)
                NoopButton("Delete session", systemImage: "trash", kind: .destructive,
                           fullWidth: true) { deleteCandidate = session }
                    .disabled(session.active || exportingId != nil)
            }
        }
    }

    private func counter(_ label: LocalizedStringKey, _ value: String) -> some View {
        VStack(spacing: NoopMetrics.space1) {
            Text(value).font(StrandFont.number(28)).foregroundStyle(StrandPalette.textPrimary)
            Text(label).font(StrandFont.caption).foregroundStyle(StrandPalette.textSecondary)
                .multilineTextAlignment(.center)
        }.frame(maxWidth: .infinity)
    }

    private func start() {
        guard model.ble.startGroundTruthRawCapture() else { return }
        if store.start(deviceId: model.ble.deviceId) == nil {
            Task { await model.ble.stopGroundTruthRawCapture() }
        }
    }

    private func stop() async {
        await model.ble.stopGroundTruthRawCapture()
        store.stop()
        await refreshRawCounts()
    }

    private func export(_ session: RawDataSessionStore.Session) async {
        guard let end = session.endedAtMs else { return }
        exportingId = session.id
        let from = Int(session.startedAtMs / 1_000), to = Int(end / 1_000)
        let chunks = await archive.pin(deviceId: session.deviceId, from: from, to: to, ble: model.ble)
        let history = await model.ble.groundTruthHistoryCSV(from: from, to: to)
        var entries = store.exportEntries(for: session, raw: [])
        entries.append(.init(name: "history-sensors.csv", data: history))
        let coverage: [String: Any] = [
            "requested_start_ts": from, "requested_end_ts": to,
            "complete": Self.covers(chunks.map { ($0.startTs, $0.endTs) }, from: from, to: to),
            "chunks": chunks.map { ["start_ts": $0.startTs, "end_ts": $0.endTs,
                                     "sample_count": $0.sampleCount, "sha256": $0.sha256] }
        ]
        if let data = try? JSONSerialization.data(withJSONObject: coverage, options: [.prettyPrinted, .sortedKeys]) {
            entries.append(.init(name: "imu-coverage.json", data: data))
        }
        for chunk in chunks {
            if let data = try? Data(contentsOf: archive.file(chunk)) {
                entries.append(.init(name: "imu/\(chunk.id).imuc", data: data))
            }
        }
        let result = await FileExport.exportBundle(entries: entries,
                                                    suggestedName: "noop-ground-truth-\(session.id).zip")
        if result == nil { exportError = "The export file could not be created or shared." }
        else { store.markExported(session.id) }
        exportingId = nil
    }

    private func delete(_ session: RawDataSessionStore.Session) async {
        guard session.endedAtMs != nil else { return }
        // Raw chunks may overlap another session; retention cleanup owns physical deletion.
        store.removeMetadata(session.id)
        rawBatchCounts[session.id] = nil
    }

    private func deleteAll() async {
        for session in store.sessions where !session.active { await delete(session) }
    }

    private func refreshRawCounts() async {
        for session in store.sessions {
            guard let end = session.endedAtMs else { continue }
            rawBatchCounts[session.id] = await model.ble.groundTruthRawBatches(
                from: Int(session.startedAtMs / 1_000), to: Int(end / 1_000)).count
        }
    }

    private static func time(_ ms: Int64) -> String {
        Date(timeIntervalSince1970: Double(ms) / 1_000).formatted(date: .omitted, time: .shortened)
    }

    private static func range(_ session: RawDataSessionStore.Session) -> String {
        let start = Date(timeIntervalSince1970: Double(session.startedAtMs) / 1_000)
        let date = start.formatted(date: .numeric, time: .omitted)
        let end = session.endedAtMs.map(time) ?? "…"
        return "\(date) · \(time(session.startedAtMs))–\(end)"
    }

    private static func covers(_ intervals: [(Int, Int)], from: Int, to: Int) -> Bool {
        var cursor = from
        for (start, end) in intervals.sorted(by: { $0.0 < $1.0 }) {
            if start > cursor { return false }
            if end >= cursor { cursor = end + 1 }
            if cursor > to { return true }
        }
        return cursor > to
    }
}

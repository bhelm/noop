import Combine
import Foundation
import WhoopStore

/// Persistent, user-controlled 5/MG capture sessions. This is the iOS/macOS twin of Android's
/// GroundTruthCollector: raw capture is useful on its own, while step/stair labels are optional.
@MainActor
final class RawDataSessionStore: ObservableObject {
    enum LabelKind: String, Codable {
        case start, stop, step, stair
        case undoStep = "undo_step"
        case undoStair = "undo_stair"
        case excludeWindow = "exclude_window"
    }

    struct Event: Codable, Identifiable, Equatable {
        var id = UUID()
        let atMs: Int64
        let kind: LabelKind
        let stepsTotal: Int
        let stairsTotal: Int
        var fromMs: Int64?
        var toMs: Int64?
    }

    struct Session: Codable, Identifiable, Equatable {
        let id: String
        let deviceId: String
        let startedAtMs: Int64
        var endedAtMs: Int64?
        var steps: Int
        var stairs: Int
        var excludedWindows: Int
        var comment: String
        var exported: Bool
        var events: [Event]

        var active: Bool { endedAtMs == nil }

        static func == (lhs: Session, rhs: Session) -> Bool {
            lhs.id == rhs.id && lhs.endedAtMs == rhs.endedAtMs && lhs.steps == rhs.steps
                && lhs.stairs == rhs.stairs && lhs.excludedWindows == rhs.excludedWindows
                && lhs.comment == rhs.comment && lhs.exported == rhs.exported
        }
    }

    @Published private(set) var sessions: [Session] = []
    var active: Session? { sessions.first(where: \.active) }

    private let directory: URL
    private let encoder: JSONEncoder
    private let decoder = JSONDecoder()

    init(directory override: URL? = nil, fileManager: FileManager = .default) {
        let base = (try? fileManager.url(for: .applicationSupportDirectory, in: .userDomainMask,
                                         appropriateFor: nil, create: true)) ?? fileManager.temporaryDirectory
        directory = override ?? base.appendingPathComponent("OpenWhoop/RawDataSessions", isDirectory: true)
        try? fileManager.createDirectory(at: directory, withIntermediateDirectories: true)
        encoder = JSONEncoder()
        encoder.outputFormatting = [.sortedKeys]
        reload()
    }

    func reload() {
        let urls = (try? FileManager.default.contentsOfDirectory(at: directory,
                    includingPropertiesForKeys: nil)) ?? []
        sessions = urls.filter { $0.pathExtension == "json" }
            .compactMap { try? Data(contentsOf: $0) }
            .compactMap { try? decoder.decode(Session.self, from: $0) }
            .sorted { $0.startedAtMs > $1.startedAtMs }
    }

    @discardableResult
    func start(deviceId: String, now: Date = Date()) -> Session? {
        guard active == nil else { return nil }
        let millis = Int64(now.timeIntervalSince1970 * 1_000)
        let started = Event(atMs: millis, kind: .start, stepsTotal: 0, stairsTotal: 0)
        let session = Session(id: String(millis), deviceId: deviceId, startedAtMs: millis,
                              endedAtMs: nil, steps: 0, stairs: 0, excludedWindows: 0,
                              comment: "", exported: false, events: [started])
        sessions.insert(session, at: 0)
        persist(session)
        return session
    }

    func stop(now: Date = Date()) {
        mutateActive { session in
            let millis = Int64(now.timeIntervalSince1970 * 1_000)
            session.endedAtMs = millis
            session.events.append(Event(atMs: millis, kind: .stop,
                                        stepsTotal: session.steps, stairsTotal: session.stairs))
        }
    }

    func record(_ kind: LabelKind, now: Date = Date()) {
        guard kind == .step || kind == .stair else { return }
        mutateActive { session in
            session.steps += 1
            if kind == .stair { session.stairs += 1 }
            session.events.append(Event(atMs: Int64(now.timeIntervalSince1970 * 1_000), kind: kind,
                                        stepsTotal: session.steps, stairsTotal: session.stairs))
        }
    }

    func undo(now: Date = Date()) {
        mutateActive { session in
            guard let prior = session.events.last,
                  prior.kind == .step || prior.kind == .stair else { return }
            let undo: LabelKind = prior.kind == .stair ? .undoStair : .undoStep
            session.steps = max(0, session.steps - 1)
            if prior.kind == .stair { session.stairs = max(0, session.stairs - 1) }
            session.events.append(Event(atMs: Int64(now.timeIntervalSince1970 * 1_000), kind: undo,
                                        stepsTotal: session.steps, stairsTotal: session.stairs))
        }
    }

    func excludeLast(minutes: Int, now: Date = Date()) {
        guard (1...240).contains(minutes) else { return }
        mutateActive { session in
            let to = Int64(now.timeIntervalSince1970 * 1_000)
            let from = max(session.startedAtMs, to - Int64(minutes) * 60_000)
            session.excludedWindows += 1
            session.events.append(Event(atMs: to, kind: .excludeWindow, stepsTotal: session.steps,
                                        stairsTotal: session.stairs, fromMs: from, toMs: to))
        }
    }

    func setComment(_ comment: String, sessionId: String) {
        mutate(sessionId) { $0.comment = String(comment.prefix(4_000)) }
    }

    func markExported(_ sessionId: String) { mutate(sessionId) { $0.exported = true } }

    func removeMetadata(_ sessionId: String) {
        guard sessions.first(where: { $0.id == sessionId })?.active != true else { return }
        try? FileManager.default.removeItem(at: file(sessionId))
        sessions.removeAll { $0.id == sessionId }
    }

    private func mutateActive(_ body: (inout Session) -> Void) {
        guard let index = sessions.firstIndex(where: \.active) else { return }
        body(&sessions[index])
        persist(sessions[index])
    }

    private func mutate(_ id: String, _ body: (inout Session) -> Void) {
        guard let index = sessions.firstIndex(where: { $0.id == id }) else { return }
        body(&sessions[index])
        persist(sessions[index])
    }

    private func persist(_ session: Session) {
        guard let data = try? encoder.encode(session) else { return }
        try? data.write(to: file(session.id), options: .atomic)
    }

    private func file(_ id: String) -> URL { directory.appendingPathComponent("session-\(id).json") }

    func exportEntries(for session: Session,
                       raw: [(RawBatchMeta, [[UInt8]])]) -> [FileExport.BundleEntry] {
        let meta: [String: Any] = [
            "schema_version": 3, "capture_kind": "whoop_5mg_raw_data",
            "session_id": session.id, "device_id": session.deviceId,
            "started_at_ms": session.startedAtMs, "ended_at_ms": session.endedAtMs ?? session.startedAtMs,
            "manual_steps": session.steps, "manual_stairs": session.stairs,
            "comment": session.comment, "excluded_windows": session.excludedWindows,
            "sensor_export_available": !raw.isEmpty,
            "device_family": "iOS",
            "app_version": Bundle.main.infoDictionary?["CFBundleShortVersionString"] as? String ?? "?",
            "manual_labels": "optional; on-screen step and stair controls",
        ]
        let metaData = (try? JSONSerialization.data(withJSONObject: meta, options: [.prettyPrinted, .sortedKeys])) ?? Data()
        let eventData = session.events.compactMap { event -> Data? in
            var row: [String: Any] = ["at_ms": event.atMs, "kind": event.kind.rawValue,
                                      "steps_total": event.stepsTotal, "stairs_total": event.stairsTotal]
            if let from = event.fromMs { row["from_ms"] = from }
            if let to = event.toMs { row["to_ms"] = to }
            return try? JSONSerialization.data(withJSONObject: row, options: [.sortedKeys])
        }.reduce(into: Data()) { $0.append($1); $0.append(0x0A) }
        var rawData = Data()
        for (meta, frames) in raw {
            for (index, frame) in frames.enumerated() {
                let row: [String: Any] = ["batch_id": meta.batchId, "captured_at": meta.capturedAt,
                                          "start_ts": meta.startTs, "end_ts": meta.endTs,
                                          "frame_index": index,
                                          "hex": frame.map { String(format: "%02x", $0) }.joined()]
                if let data = try? JSONSerialization.data(withJSONObject: row, options: [.sortedKeys]) {
                    rawData.append(data); rawData.append(0x0A)
                }
            }
        }
        return [
            .init(name: "meta.json", data: metaData),
            .init(name: "events.jsonl", data: eventData),
            .init(name: "raw-frames.jsonl", data: rawData),
        ]
    }
}

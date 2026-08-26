import Compression
import Foundation
import WhoopProtocol

/// Persisted routing table plus append-only, independently compressed IMU session streams.
@MainActor
final class ImuSessionFileStore {
    static let shared = ImuSessionFileStore()

    private struct Window: Codable {
        let id, deviceId: String
        let from: Int64
        var to: Int64?
    }
    private let defaults = UserDefaults.standard
    private let key = "imu-session-windows-v1"
    private let directory: URL
    private var seen: [String: Set<Int64>] = [:]

    private init() {
        let fm = FileManager.default
        let base = (try? fm.url(for: .applicationSupportDirectory, in: .userDomainMask,
                                appropriateFor: nil, create: true)) ?? fm.temporaryDirectory
        directory = base.appendingPathComponent("OpenWhoop/RawDataSessions", isDirectory: true)
        try? fm.createDirectory(at: directory, withIntermediateDirectories: true)
    }

    func start(id: String, deviceId: String, fromMs: Int64) {
        var value = windows().filter { $0.id != id }
        value.append(Window(id: id, deviceId: deviceId, from: fromMs / 1_000, to: nil)); save(value)
    }

    func complete(id: String, toMs: Int64) {
        var value = windows()
        if let index = value.firstIndex(where: { $0.id == id }) { value[index].to = toMs / 1_000; save(value) }
    }

    func register(id: String, deviceId: String, fromMs: Int64, toMs: Int64) {
        var value = windows().filter { $0.id != id }
        value.append(Window(id: id, deviceId: deviceId, from: fromMs / 1_000, to: toMs / 1_000)); save(value)
    }

    func remove(id: String) { save(windows().filter { $0.id != id }); seen[id] = nil }

    @discardableResult
    func append(deviceId: String, frame: [UInt8], receivedAtMs: Int64) -> Int {
        guard let ts = Whoop5RawImu.baseTs(frame), Whoop5RawImu.rawColumns(frame) != nil else { return 0 }
        var count = 0
        for window in windows() where window.deviceId == deviceId && Int64(ts) >= window.from
            && (window.to == nil || Int64(ts) <= window.to!) {
            var timestamps = seen[window.id] ?? scan(window.id)
            guard timestamps.insert(Int64(ts)).inserted,
                  let compressed = Self.deflate(Data(frame)) else { continue }
            var record = Data()
            record.appendBigEndian(Int64(ts)); record.appendBigEndian(receivedAtMs)
            record.appendBigEndian(Int32(frame.count)); record.appendBigEndian(Int32(compressed.count))
            record.append(compressed)
            let url = file(window.id)
            if !FileManager.default.fileExists(atPath: url.path) { FileManager.default.createFile(atPath: url.path, contents: nil) }
            guard let handle = try? FileHandle(forWritingTo: url) else { continue }
            do {
                try handle.seekToEnd()
                try handle.write(contentsOf: record)
                try handle.close()
                timestamps.insert(Int64(ts))
                seen[window.id] = timestamps
                count += 1
            } catch { try? handle.close() }
        }
        return count
    }

    func file(_ id: String) -> URL { directory.appendingPathComponent("realtime-imu-\(id).imus") }

    private func windows() -> [Window] {
        guard let data = defaults.data(forKey: key) else { return [] }
        return (try? JSONDecoder().decode([Window].self, from: data)) ?? []
    }
    private func save(_ value: [Window]) { defaults.set(try? JSONEncoder().encode(value), forKey: key) }

    private func scan(_ id: String) -> Set<Int64> {
        guard let data = try? Data(contentsOf: file(id)) else { return [] }
        var result = Set<Int64>(), offset = 0
        while offset + 24 <= data.count {
            let ts: Int64 = data.bigEndian(at: offset); let compressed: Int32 = data.bigEndian(at: offset + 20)
            guard compressed >= 0, offset + 24 + Int(compressed) <= data.count else { break }
            result.insert(ts); offset += 24 + Int(compressed)
        }
        return result
    }

    private static func deflate(_ input: Data) -> Data? {
        let capacity = max(input.count + 128, 256); var output = Data(count: capacity)
        let written = output.withUnsafeMutableBytes { dst in input.withUnsafeBytes { src in
            compression_encode_buffer(dst.bindMemory(to: UInt8.self).baseAddress!, capacity,
                                      src.bindMemory(to: UInt8.self).baseAddress!, input.count,
                                      nil, COMPRESSION_ZLIB)
        }}
        guard written > 0 else { return nil }; output.count = written; return output
    }
}

private extension Data {
    mutating func appendBigEndian<T: FixedWidthInteger>(_ value: T) {
        var value = value.bigEndian; Swift.withUnsafeBytes(of: &value) { append(contentsOf: $0) }
    }
    func bigEndian<T: FixedWidthInteger>(at offset: Int) -> T {
        self[offset..<(offset + MemoryLayout<T>.size)].reduce(T.zero) { ($0 << 8) | T($1) }
    }
}

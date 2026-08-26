import Compression
import Foundation
import WhoopProtocol

/// Persisted routing table plus append-only, 30-frame compressed IMU session blocks.
@MainActor
final class ImuSessionFileStore {
    static let shared = ImuSessionFileStore()

    private struct Window: Codable {
        let id, deviceId: String
        let from: Int64
        var to: Int64?
    }
    private struct Record { let ts, receivedAtMs: Int64; let frame: [UInt8] }
    private static let blockFrames = 30
    private let defaults = UserDefaults.standard
    private let key = "imu-session-windows-v1"
    private let directory: URL
    private var seen: [String: Set<Int64>] = [:]
    private var pending: [String: [Record]] = [:]

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
        flush(id)
        var value = windows()
        if let index = value.firstIndex(where: { $0.id == id }) { value[index].to = toMs / 1_000; save(value) }
    }

    func register(id: String, deviceId: String, fromMs: Int64, toMs: Int64) {
        var value = windows().filter { $0.id != id }
        value.append(Window(id: id, deviceId: deviceId, from: fromMs / 1_000, to: toMs / 1_000)); save(value)
    }

    func remove(id: String) {
        pending[id] = nil
        save(windows().filter { $0.id != id })
        seen[id] = nil
    }

    func prepareForRead(_ id: String) { flush(id) }

    @discardableResult
    func append(deviceId: String, frame: [UInt8], receivedAtMs: Int64) -> Int {
        guard let ts = Whoop5RawImu.baseTs(frame), Whoop5RawImu.rawColumns(frame) != nil else { return 0 }
        var count = 0
        for window in windows() where window.deviceId == deviceId && Int64(ts) >= window.from
            && (window.to == nil || Int64(ts) <= window.to!) {
            var timestamps = seen[window.id] ?? scan(window.id)
            guard timestamps.insert(Int64(ts)).inserted else { continue }
            seen[window.id] = timestamps
            pending[window.id, default: []].append(Record(ts: Int64(ts), receivedAtMs: receivedAtMs,
                                                          frame: frame))
            if pending[window.id]!.count >= Self.blockFrames { flush(window.id) }
            count += 1
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
        while offset + 12 <= data.count {
            let count: Int32 = data.bigEndian(at: offset)
            let rawSize: Int32 = data.bigEndian(at: offset + 4)
            let compressedSize: Int32 = data.bigEndian(at: offset + 8)
            offset += 12
            guard count > 0, count <= Self.blockFrames, rawSize > 0, compressedSize > 0,
                  offset + Int(compressedSize) <= data.count,
                  let raw = Self.inflate(data[offset..<(offset + Int(compressedSize))], size: Int(rawSize)) else { break }
            offset += Int(compressedSize)
            var rawOffset = 0
            for _ in 0..<count where rawOffset + 20 <= raw.count {
                let ts: Int64 = raw.bigEndian(at: rawOffset)
                let length: Int32 = raw.bigEndian(at: rawOffset + 16)
                guard length > 0, rawOffset + 20 + Int(length) <= raw.count else { break }
                result.insert(ts); rawOffset += 20 + Int(length)
            }
        }
        return result
    }

    private func flush(_ id: String) {
        guard let records = pending.removeValue(forKey: id), !records.isEmpty else { return }
        var raw = Data()
        for record in records {
            raw.appendBigEndian(record.ts); raw.appendBigEndian(record.receivedAtMs)
            raw.appendBigEndian(Int32(record.frame.count)); raw.append(contentsOf: record.frame)
        }
        guard let compressed = Self.deflate(raw) else { pending[id] = records; return }
        var block = Data()
        block.appendBigEndian(Int32(records.count)); block.appendBigEndian(Int32(raw.count))
        block.appendBigEndian(Int32(compressed.count)); block.append(compressed)
        let url = file(id)
        if !FileManager.default.fileExists(atPath: url.path) { FileManager.default.createFile(atPath: url.path, contents: nil) }
        guard let handle = try? FileHandle(forWritingTo: url) else { pending[id] = records; return }
        do {
            try handle.seekToEnd(); try handle.write(contentsOf: block); try handle.close()
        } catch { try? handle.close(); pending[id] = records }
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

    private static func inflate(_ input: Data.SubSequence, size: Int) -> Data? {
        var output = Data(count: size)
        let written = output.withUnsafeMutableBytes { dst in input.withUnsafeBytes { src in
            compression_decode_buffer(dst.bindMemory(to: UInt8.self).baseAddress!, size,
                                      src.bindMemory(to: UInt8.self).baseAddress!, input.count,
                                      nil, COMPRESSION_ZLIB)
        }}
        return written == size ? output : nil
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

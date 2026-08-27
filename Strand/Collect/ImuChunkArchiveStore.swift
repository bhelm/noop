import CryptoKit
import Compression
import Foundation
import WhoopProtocol
import WhoopStore

/// Apple twin of Android ImuChunkStore. ZIP entries and samples.bin layout are platform-identical.
@MainActor
final class ImuChunkArchiveStore {
    static let formatVersion = 1
    static let sampleRate = 100
    private let directory: URL

    init() {
        let base = (try? FileManager.default.url(for: .applicationSupportDirectory, in: .userDomainMask,
                                                  appropriateFor: nil, create: true))
            ?? FileManager.default.temporaryDirectory
        directory = base.appendingPathComponent("OpenWhoop/imu-chunks", isDirectory: true)
        try? FileManager.default.createDirectory(at: directory, withIntermediateDirectories: true)
    }

    func pin(sessionId: String, deviceId: String, from: Int, to: Int, ble: BLEManager) async -> [ImuChunkMeta] {
        ImuSessionFileStore.shared.prepareForRead(sessionId)
        let existing = await ble.groundTruthImuChunks(from: from, to: to)
            .filter { Self.isOwned($0.id, by: sessionId) && $0.pinnedUntil == Int.max
                && FileManager.default.fileExists(atPath: file($0).path) }
        if let exact = existing.first(where: { $0.startTs == from && $0.endTs == to
            && $0.sampleCount >= ($0.endTs - $0.startTs + 1) * $0.sampleRate }) {
            _ = await deleteOwned(sessionId: sessionId, keeping: [exact.id], ble: ble)
            return [exact]
        }
        let rows = readSessionRows(sessionId: sessionId, from: from, to: to)
        guard !rows.isEmpty else { return Self.bounded(existing, from: from, to: to) }
        let startTs = rows.map(\.ts).min()!, endTs = rows.map(\.ts).max()!

        var samples = Data()
        for row in rows {
            samples.appendBigEndian(Int64(row.ts))
            samples.appendBigEndian(Int32(row.cols.count * 2))
            for value in row.cols {
                samples.append(UInt8(truncatingIfNeeded: value))
                samples.append(UInt8(truncatingIfNeeded: value >> 8))
            }
        }
        let manifest: [String: Any] = [
            "format": "NOOPIMU", "version": Self.formatVersion,
            "sample_rate_hz": Self.sampleRate,
            "axes": ["ax", "ay", "az", "gx", "gy", "gz"],
            "layout": "column-major-int16-le", "row_count": rows.count,
            "ordering": "unspecified", "timestamp_source": "strap",
            "start_ts": startTs, "end_ts": endTs,
        ]
        guard let manifestData = try? JSONSerialization.data(withJSONObject: manifest, options: [.prettyPrinted, .sortedKeys]),
              let staged = FileExport.zipData(entries: [
                .init(name: "manifest.json", data: manifestData), .init(name: "samples.bin", data: samples),
              ], baseName: UUID().uuidString) else { return [] }
        let id = Self.prefix(sessionId) + UUID().uuidString
        let destination = directory.appendingPathComponent("\(id).imuc")
        do { try FileManager.default.moveItem(at: staged, to: destination) } catch { return [] }
        let bytes = (try? Data(contentsOf: destination)) ?? Data()
        let base = directory.deletingLastPathComponent().deletingLastPathComponent()
        let relative = destination.path.replacingOccurrences(of: base.path + "/", with: "")
        let chunk = ImuChunkMeta(
            id: id, deviceId: deviceId, startTs: startTs, endTs: endTs,
            sampleCount: rows.count * Self.sampleRate, sampleRate: Self.sampleRate,
            formatVersion: Self.formatVersion, codec: "zip-deflate", relativePath: relative,
            byteSize: bytes.count, sha256: SHA256.hash(data: bytes).map { String(format: "%02x", $0) }.joined(),
            createdAt: Int(Date().timeIntervalSince1970), pinnedUntil: Int.max)
        await ble.registerGroundTruthImuChunk(chunk)
        _ = await deleteOwned(sessionId: sessionId, keeping: [id], ble: ble)
        return [chunk]
    }

    func deleteOwned(sessionId: String, keeping: Set<String> = [], ble: BLEManager) async -> Bool {
        guard let catalog = await ble.groundTruthImuChunks(idPrefix: Self.prefix(sessionId)) else { return false }
        let catalogIds = Set(catalog.map(\.id))
        let fileIds = Set((try? FileManager.default.contentsOfDirectory(at: directory,
            includingPropertiesForKeys: nil))?.compactMap { url in
                url.pathExtension == "imuc" && Self.isOwned(url.deletingPathExtension().lastPathComponent,
                                                              by: sessionId)
                    ? url.deletingPathExtension().lastPathComponent : nil
            } ?? [])
        for id in catalogIds.union(fileIds).subtracting(keeping) {
            let url = directory.appendingPathComponent("\(id).imuc")
            guard await Self.deleteRetrySafe(
                fileExists: FileManager.default.fileExists(atPath: url.path),
                deleteFile: { (try? FileManager.default.removeItem(at: url)) != nil },
                deleteCatalog: { await ble.deleteGroundTruthImuChunk(id: id) }
            ) else { return false }
        }
        return true
    }

    func deleteDevice(_ deviceId: String, store: WhoopStore) async -> Bool {
        guard let chunks = try? await store.imuChunks(deviceId: deviceId, from: Int.min, to: Int.max) else {
            return false
        }
        for chunk in chunks {
            let url = file(chunk)
            guard await Self.deleteRetrySafe(
                fileExists: FileManager.default.fileExists(atPath: url.path),
                deleteFile: { (try? FileManager.default.removeItem(at: url)) != nil },
                deleteCatalog: {
                    do { try await store.deleteImuChunk(id: chunk.id); return true }
                    catch { return false }
                }
            ) else { return false }
        }
        return true
    }

    func file(_ chunk: ImuChunkMeta) -> URL {
        let base = directory.deletingLastPathComponent().deletingLastPathComponent()
        return base.appendingPathComponent(chunk.relativePath)
    }

    static func prefix(_ sessionId: String) -> String { "\(sessionId)--" }
    static func isOwned(_ chunkId: String, by sessionId: String) -> Bool {
        chunkId.hasPrefix(prefix(sessionId))
    }
    static func bounded(_ chunks: [ImuChunkMeta], from: Int, to: Int) -> [ImuChunkMeta] {
        chunks.filter { $0.startTs >= from && $0.endTs <= to }
    }
    static func deleteRetrySafe(fileExists: Bool, deleteFile: () -> Bool,
                                deleteCatalog: () async -> Bool) async -> Bool {
        if fileExists && !deleteFile() { return false }
        return await deleteCatalog()
    }

    private func readSessionRows(sessionId: String, from: Int, to: Int) -> [(ts: Int, cols: [Int16])] {
        let url = ImuSessionFileStore.shared.file(sessionId)
        guard let data = try? Data(contentsOf: url) else { return [] }
        let bytes = [UInt8](data); var offset = 0
        var rows: [(Int, [Int16])] = []
        while offset + 12 <= bytes.count {
            let count = int32(bytes, offset); let rawLength = int32(bytes, offset + 4)
            let compressedLength = int32(bytes, offset + 8); offset += 12
            guard count > 0, count <= 30, rawLength > 0, compressedLength > 0,
                  offset + compressedLength <= bytes.count else { break }
            let compressed = Data(bytes[offset..<offset + compressedLength]); offset += compressedLength
            var decoded = Data(count: rawLength)
            let written = decoded.withUnsafeMutableBytes { dst in compressed.withUnsafeBytes { src in
                compression_decode_buffer(dst.bindMemory(to: UInt8.self).baseAddress!, rawLength,
                    src.bindMemory(to: UInt8.self).baseAddress!, compressedLength, nil, COMPRESSION_ZLIB)
            }}
            guard written == rawLength else { continue }
            let raw = [UInt8](decoded); var rawOffset = 0
            for _ in 0..<count {
                guard rawOffset + 20 <= raw.count else { break }
                let headerTs = Int64(bigEndianBytes: raw, at: rawOffset); rawOffset += 16
                let length = int32(raw, rawOffset); rawOffset += 4
                guard length > 0, rawOffset + length <= raw.count else { break }
                let frame = Array(raw[rawOffset..<rawOffset + length]); rawOffset += length
                guard let ts = Whoop5RawImu.baseTs(frame), Int64(ts) == headerTs, ts >= from, ts <= to,
                      let cols = Whoop5RawImu.rawColumns(frame) else { continue }
                rows.append((ts, cols))
            }
        }
        return rows
    }

    private func int32(_ bytes: [UInt8], _ offset: Int) -> Int {
        Int(bytes[offset]) << 24 | Int(bytes[offset + 1]) << 16
            | Int(bytes[offset + 2]) << 8 | Int(bytes[offset + 3])
    }
}

private extension Int64 {
    init(bigEndianBytes bytes: [UInt8], at offset: Int) {
        self = bytes[offset..<(offset + 8)].reduce(0) { ($0 << 8) | Int64($1) }
    }
}

private extension Data {
    mutating func appendBigEndian<T: FixedWidthInteger>(_ value: T) {
        var v = value.bigEndian
        Swift.withUnsafeBytes(of: &v) { append(contentsOf: $0) }
    }
}

import CryptoKit
import Foundation
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

    func pin(deviceId: String, from: Int, to: Int, ble: BLEManager) async -> [ImuChunkMeta] {
        let existing = await ble.groundTruthImuChunks(from: from, to: to)
            .filter { $0.pinnedUntil == Int.max && file($0).isFileURL && FileManager.default.fileExists(atPath: file($0).path) }
        if existing.contains(where: { $0.startTs <= from && $0.endTs >= to }) { return existing }
        let rows = await ble.groundTruthRawImuSamples(from: from, to: to)
        guard !rows.isEmpty else { return existing }

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
            "start_ts": rows.first!.ts, "end_ts": rows.last!.ts,
        ]
        guard let manifestData = try? JSONSerialization.data(withJSONObject: manifest, options: [.prettyPrinted, .sortedKeys]),
              let staged = FileExport.zipData(entries: [
                .init(name: "manifest.json", data: manifestData), .init(name: "samples.bin", data: samples),
              ], baseName: UUID().uuidString) else { return [] }
        let id = UUID().uuidString
        let destination = directory.appendingPathComponent("\(id).imuc")
        do { try FileManager.default.moveItem(at: staged, to: destination) } catch { return [] }
        let bytes = (try? Data(contentsOf: destination)) ?? Data()
        let base = directory.deletingLastPathComponent().deletingLastPathComponent()
        let relative = destination.path.replacingOccurrences(of: base.path + "/", with: "")
        let chunk = ImuChunkMeta(
            id: id, deviceId: deviceId, startTs: rows.first!.ts, endTs: rows.last!.ts,
            sampleCount: rows.count * Self.sampleRate, sampleRate: Self.sampleRate,
            formatVersion: Self.formatVersion, codec: "zip-deflate", relativePath: relative,
            byteSize: bytes.count, sha256: SHA256.hash(data: bytes).map { String(format: "%02x", $0) }.joined(),
            createdAt: Int(Date().timeIntervalSince1970), pinnedUntil: Int.max)
        await ble.registerGroundTruthImuChunk(chunk)
        return await ble.groundTruthImuChunks(from: from, to: to)
            .filter { FileManager.default.fileExists(atPath: file($0).path) }
    }

    func file(_ chunk: ImuChunkMeta) -> URL {
        let base = directory.deletingLastPathComponent().deletingLastPathComponent()
        return base.appendingPathComponent(chunk.relativePath)
    }
}

private extension Data {
    mutating func appendBigEndian<T: FixedWidthInteger>(_ value: T) {
        var v = value.bigEndian
        Swift.withUnsafeBytes(of: &v) { append(contentsOf: $0) }
    }
}

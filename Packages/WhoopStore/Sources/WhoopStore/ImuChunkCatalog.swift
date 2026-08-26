import Foundation
import GRDB

public struct ImuChunkMeta: Codable, Equatable, Sendable {
    public let id: String
    public let deviceId: String
    public let startTs: Int
    public let endTs: Int
    public let sampleCount: Int
    public let sampleRate: Int
    public let formatVersion: Int
    public let codec: String
    public let relativePath: String
    public let byteSize: Int
    public let sha256: String
    public let createdAt: Int
    public let pinnedUntil: Int?

    public init(id: String, deviceId: String, startTs: Int, endTs: Int, sampleCount: Int,
                sampleRate: Int, formatVersion: Int, codec: String, relativePath: String,
                byteSize: Int, sha256: String, createdAt: Int, pinnedUntil: Int?) {
        self.id = id; self.deviceId = deviceId; self.startTs = startTs; self.endTs = endTs
        self.sampleCount = sampleCount; self.sampleRate = sampleRate; self.formatVersion = formatVersion
        self.codec = codec; self.relativePath = relativePath; self.byteSize = byteSize
        self.sha256 = sha256; self.createdAt = createdAt; self.pinnedUntil = pinnedUntil
    }
}

extension WhoopStore {
    public func upsertImuChunk(_ chunk: ImuChunkMeta) async throws {
        try syncWrite { db in
            try db.execute(sql: """
                INSERT INTO imuChunk
                  (id, deviceId, startTs, endTs, sampleCount, sampleRate, formatVersion, codec,
                   relativePath, byteSize, sha256, createdAt, pinnedUntil)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                  relativePath=excluded.relativePath, byteSize=excluded.byteSize,
                  sha256=excluded.sha256, pinnedUntil=excluded.pinnedUntil
                """, arguments: [chunk.id, chunk.deviceId, chunk.startTs, chunk.endTs,
                                  chunk.sampleCount, chunk.sampleRate, chunk.formatVersion, chunk.codec,
                                  chunk.relativePath, chunk.byteSize, chunk.sha256, chunk.createdAt,
                                  chunk.pinnedUntil])
        }
    }

    public func imuChunks(deviceId: String, from: Int, to: Int) async throws -> [ImuChunkMeta] {
        try syncRead { db in
            try Row.fetchAll(db, sql: """
                SELECT * FROM imuChunk
                WHERE deviceId = ? AND endTs >= ? AND startTs <= ? ORDER BY startTs
                """, arguments: [deviceId, from, to]).map { row in
                    ImuChunkMeta(id: row["id"], deviceId: row["deviceId"], startTs: row["startTs"],
                                 endTs: row["endTs"], sampleCount: row["sampleCount"],
                                 sampleRate: row["sampleRate"], formatVersion: row["formatVersion"],
                                 codec: row["codec"], relativePath: row["relativePath"],
                                 byteSize: row["byteSize"], sha256: row["sha256"],
                                 createdAt: row["createdAt"], pinnedUntil: row["pinnedUntil"])
                }
        }
    }
}

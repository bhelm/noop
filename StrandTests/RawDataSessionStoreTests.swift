import XCTest
@testable import Strand
import WhoopStore

@MainActor
final class RawDataSessionStoreTests: XCTestCase {
    private func temporaryDirectory() throws -> URL {
        let url = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
        try FileManager.default.createDirectory(at: url, withIntermediateDirectories: true)
        addTeardownBlock { try? FileManager.default.removeItem(at: url) }
        return url
    }

    func testRawSessionLifecycleAndReload() throws {
        let directory = try temporaryDirectory()
        let store = RawDataSessionStore(directory: directory)
        XCTAssertNotNil(store.start(deviceId: "strap", now: Date(timeIntervalSince1970: 100)))
        store.stop(now: Date(timeIntervalSince1970: 106))

        let session = try XCTUnwrap(store.sessions.first)
        XCTAssertEqual(session.endedAtMs, 106_000)
        XCTAssertEqual(session.events.map(\.kind), ["start", "stop"])

        let reloaded = RawDataSessionStore(directory: directory)
        XCTAssertEqual(reloaded.sessions.first, session)
    }

    func testMarkersCanBeAddedEditedDeletedAndExported() throws {
        let store = RawDataSessionStore(directory: try temporaryDirectory())
        let session = try XCTUnwrap(store.start(deviceId: "strap", now: Date(timeIntervalSince1970: 100)))
        let marker = try XCTUnwrap(store.addMarker(sessionId: session.id,
            at: Date(timeIntervalSince1970: 102), type: "issue", text: "Bluetooth off"))
        store.updateMarker(sessionId: session.id, markerId: try XCTUnwrap(marker.markerId),
                           at: Date(timeIntervalSince1970: 103), type: "moment", text: "Recovered")
        store.stop(now: Date(timeIntervalSince1970: 106))

        let updated = try XCTUnwrap(store.sessions.first)
        let saved = try XCTUnwrap(updated.events.first(where: { $0.kind == "marker" }))
        XCTAssertEqual(saved.atMs, 103_000)
        XCTAssertEqual(saved.markerType, "moment")
        XCTAssertEqual(saved.text, "Recovered")

        let events = try XCTUnwrap(store.exportEntries(for: updated, raw: [])
            .first(where: { $0.name == "events.jsonl" }))
        XCTAssertTrue(String(decoding: events.data, as: UTF8.self).contains("\"marker_type\":\"moment\""))
        let meta = try XCTUnwrap(store.exportEntries(for: updated, raw: [])
            .first(where: { $0.name == "meta.json" }))
        let object = try XCTUnwrap(JSONSerialization.jsonObject(with: meta.data) as? [String: Any])
        let markers = try XCTUnwrap(object["markers"] as? [[String: Any]])
        XCTAssertEqual(markers.first?["type"] as? String, "moment")

        store.deleteMarker(sessionId: session.id, markerId: try XCTUnwrap(saved.markerId))
        XCTAssertFalse(try XCTUnwrap(store.sessions.first).events.contains(where: { $0.kind == "marker" }))
    }

    func testMarkerTimeIsClampedToSession() throws {
        let store = RawDataSessionStore(directory: try temporaryDirectory())
        let session = try XCTUnwrap(store.start(deviceId: "strap", now: Date(timeIntervalSince1970: 100)))
        store.stop(now: Date(timeIntervalSince1970: 110))
        let marker = try XCTUnwrap(store.addMarker(sessionId: session.id,
            at: Date(timeIntervalSince1970: 200), type: "end", text: ""))
        XCTAssertEqual(marker.atMs, 110_000)
    }

    func testEditedRangeFiltersExportWithoutDestroyingMarkers() throws {
        let store = RawDataSessionStore(directory: try temporaryDirectory())
        let session = try XCTUnwrap(store.start(deviceId: "strap", now: Date(timeIntervalSince1970: 100)))
        _ = store.addMarker(sessionId: session.id, at: Date(timeIntervalSince1970: 102),
                            type: "start", text: "outside")
        _ = store.addMarker(sessionId: session.id, at: Date(timeIntervalSince1970: 105),
                            type: "moment", text: "inside")
        store.stop(now: Date(timeIntervalSince1970: 110))
        store.setRange(sessionId: session.id, from: Date(timeIntervalSince1970: 104),
                       to: Date(timeIntervalSince1970: 108))

        let edited = try XCTUnwrap(store.sessions.first)
        XCTAssertEqual(edited.events.filter { $0.kind == "marker" }.count, 2)
        let events = try XCTUnwrap(store.exportEntries(for: edited, raw: [])
            .first(where: { $0.name == "events.jsonl" }))
        let text = String(decoding: events.data, as: UTF8.self)
        XCTAssertFalse(text.contains("outside"))
        XCTAssertTrue(text.contains("inside"))
    }

    func testExportReportsSensorAvailabilityFromAssembledPayload() throws {
        let store = RawDataSessionStore(directory: try temporaryDirectory())
        _ = store.start(deviceId: "strap", now: Date(timeIntervalSince1970: 100))
        store.stop(now: Date(timeIntervalSince1970: 110))
        let session = try XCTUnwrap(store.sessions.first)
        let meta = try XCTUnwrap(store.exportEntries(for: session, raw: [], sensorAvailable: true)
            .first(where: { $0.name == "meta.json" }))
        let object = try XCTUnwrap(JSONSerialization.jsonObject(with: meta.data) as? [String: Any])
        XCTAssertEqual(object["sensor_export_available"] as? Bool, true)
    }

    func testFullSecondBoundsExcludePartialEndpointSeconds() {
        let bounds = RawDataCollectorView.fullSecondBounds(fromMs: 100_001, toMs: 104_999)
        XCTAssertEqual(bounds?.from, 101)
        XCTAssertEqual(bounds?.to, 103)
        XCTAssertNil(RawDataCollectorView.fullSecondBounds(fromMs: 100_001, toMs: 100_999))
    }

    func testChunksAreSessionOwnedAndStrictlyBounded() {
        let owned = ImuChunkMeta(id: "one--chunk", deviceId: "strap", startTs: 101, endTs: 103,
            sampleCount: 300, sampleRate: 100, formatVersion: 1, codec: "zip-deflate",
            relativePath: "owned", byteSize: 1, sha256: "a", createdAt: 0, pinnedUntil: Int.max)
        let other = ImuChunkMeta(id: "two--chunk", deviceId: "strap", startTs: 101, endTs: 103,
            sampleCount: 300, sampleRate: 100, formatVersion: 1, codec: "zip-deflate",
            relativePath: "other", byteSize: 1, sha256: "b", createdAt: 0, pinnedUntil: Int.max)
        let broad = ImuChunkMeta(id: "one--broad", deviceId: "strap", startTs: 100, endTs: 104,
            sampleCount: 500, sampleRate: 100, formatVersion: 1, codec: "zip-deflate",
            relativePath: "broad", byteSize: 1, sha256: "c", createdAt: 0, pinnedUntil: Int.max)

        XCTAssertTrue(ImuChunkArchiveStore.isOwned(owned.id, by: "one"))
        XCTAssertFalse(ImuChunkArchiveStore.isOwned(other.id, by: "one"))
        XCTAssertEqual(ImuChunkArchiveStore.bounded([owned, broad], from: 101, to: 103), [owned])
    }

    func testArchiveDeleteFailureKeepsCatalogForRetry() async {
        var operations: [String] = []
        let deleted = await ImuChunkArchiveStore.deleteRetrySafe(fileExists: true, deleteFile: {
            operations.append("file"); return false
        }, deleteCatalog: {
            operations.append("catalog"); return true
        })
        XCTAssertFalse(deleted)
        XCTAssertEqual(operations, ["file"])
    }

    func testMarkerCurrentTimeTicksOnlyForActiveSession() throws {
        let store = RawDataSessionStore(directory: try temporaryDirectory())
        _ = store.start(deviceId: "strap", now: Date(timeIntervalSince1970: 100))
        let active = try XCTUnwrap(store.sessions.first)
        XCTAssertEqual(RawDataCollectorView.markerCurrentTime(session: active,
            now: Date(timeIntervalSince1970: 105)).timeIntervalSince1970, 105)
        store.stop(now: Date(timeIntervalSince1970: 110))
        let stopped = try XCTUnwrap(store.sessions.first)
        XCTAssertEqual(RawDataCollectorView.markerCurrentTime(session: stopped,
            now: Date(timeIntervalSince1970: 999)).timeIntervalSince1970, 110)
    }

    func testActiveSessionCannotBeDeleted() throws {
        let store = RawDataSessionStore(directory: try temporaryDirectory())
        let active = try XCTUnwrap(store.start(deviceId: "strap"))
        store.removeMetadata(active.id)
        XCTAssertEqual(store.sessions.map(\.id), [active.id])
    }

    func testCompletedSessionCanBeDeleted() throws {
        let directory = try temporaryDirectory()
        let store = RawDataSessionStore(directory: directory)
        let session = try XCTUnwrap(store.start(deviceId: "strap"))
        store.stop()
        XCTAssertTrue(store.removeMetadata(session.id))
        XCTAssertTrue(store.sessions.isEmpty)
        XCTAssertTrue(RawDataSessionStore(directory: directory).sessions.isEmpty)
    }

    func testFailedSourceDeletionKeepsSessionRetryable() throws {
        let directory = try temporaryDirectory()
        let store = RawDataSessionStore(directory: directory)
        let session = try XCTUnwrap(store.start(deviceId: "strap"))
        store.stop()
        let source = ImuSessionFileStore.shared.file(session.id)
        XCTAssertTrue(FileManager.default.createFile(atPath: source.path, contents: Data("raw".utf8)))

        XCTAssertFalse(store.removeMetadata(session.id) { url in
            if url == source {
                throw CocoaError(.fileWriteNoPermission)
            }
            try FileManager.default.removeItem(at: url)
        })
        XCTAssertEqual(store.sessions.map(\.id), [session.id])
        XCTAssertEqual(RawDataSessionStore(directory: directory).sessions.map(\.id), [session.id])
        XCTAssertTrue(store.removeMetadata(session.id))
    }
}

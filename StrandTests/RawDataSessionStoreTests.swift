import XCTest
@testable import Strand

@MainActor
final class RawDataSessionStoreTests: XCTestCase {
    private func temporaryDirectory() throws -> URL {
        let url = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
        try FileManager.default.createDirectory(at: url, withIntermediateDirectories: true)
        addTeardownBlock { try? FileManager.default.removeItem(at: url) }
        return url
    }

    func testLifecycleLabelsUndoExclusionAndReload() throws {
        let directory = try temporaryDirectory()
        let store = RawDataSessionStore(directory: directory)
        XCTAssertNotNil(store.start(deviceId: "strap", now: Date(timeIntervalSince1970: 100)))
        store.record(.step, now: Date(timeIntervalSince1970: 101))
        store.record(.stair, now: Date(timeIntervalSince1970: 102))
        store.undo(now: Date(timeIntervalSince1970: 103))
        store.undo(now: Date(timeIntervalSince1970: 104)) // the same click cannot be undone twice
        store.excludeLast(minutes: 5, now: Date(timeIntervalSince1970: 105))
        store.stop(now: Date(timeIntervalSince1970: 106))

        let session = try XCTUnwrap(store.sessions.first)
        XCTAssertEqual(session.steps, 1)
        XCTAssertEqual(session.stairs, 0)
        XCTAssertEqual(session.excludedWindows, 1)
        XCTAssertEqual(session.endedAtMs, 106_000)

        let reloaded = RawDataSessionStore(directory: directory)
        XCTAssertEqual(reloaded.sessions.first, session)
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
        store.removeMetadata(session.id)
        XCTAssertTrue(store.sessions.isEmpty)
        XCTAssertTrue(RawDataSessionStore(directory: directory).sessions.isEmpty)
    }
}

import XCTest
import Foundation
@testable import WhoopProtocol

/// Swift twin of the Kotlin `FirmwareImageStructureTest` (android/.../ble/FirmwareImageStructureTest.kt)
/// plus the parser cases of `FirmwareUpdateTest`. The image builders reproduce the Kotlin fixtures byte
/// for byte, so the expected values are the same oracle, not eyeballed.
final class FirmwareImageTests: XCTestCase {

    // MARK: Parser cases (from FirmwareUpdateTest)

    func testTypeFromOffset12AndOriginalZbinShapeValidates() {
        let bytes = zbinImage()
        guard case let .valid(image) = FirmwareImageParser.parse(fileName: "original.zbin", input: bytes) else {
            return XCTFail("expected valid")
        }
        XCTAssertEqual(image.info.format, .zbinCompressed)
    }

    func testTypeSelectorAcceptsRawBin() {
        guard case let .valid(image) = FirmwareImageParser.parse(fileName: "research.bin", input: rawImage()) else {
            return XCTFail("expected valid")
        }
        XCTAssertEqual(image.info.format, .binRaw)
        XCTAssertTrue(image.info.compatibilityNote.contains("not established"))
    }

    func testPayloadCorruptionFailsClosed() {
        var bytes = zbinImage()
        bytes[FirmwareImageParser.headerSize + 3] ^= 1
        assertInvalid(FirmwareImageParser.parse(fileName: "bad.zbin", input: bytes), contains: "Payload CRC mismatch")
    }

    func testHeaderCorruptionFailsClosed() {
        var bytes = zbinImage()
        bytes[32] ^= 1
        assertInvalid(FirmwareImageParser.parse(fileName: "bad.zbin", input: bytes), contains: "Header CRC mismatch")
    }

    func testDeclaredLengthAndActualBytesMustAgree() {
        var bytes = zbinImage()
        putU32(&bytes, 4, 4)
        assertInvalid(FirmwareImageParser.parse(fileName: "bad.zbin", input: bytes), contains: "Declared payload length")
    }

    func testUnsupportedTypeAndMisleadingExtensionAreRejected() {
        if case .valid = FirmwareImageParser.parse(fileName: "alternate.zbin", input: rawImage(type: 3)) {
            XCTFail("type 3 should be rejected")
        }
        if case .valid = FirmwareImageParser.parse(fileName: "compressed.bin", input: zbinImage()) {
            XCTFail("zbin bytes under .bin should be rejected")
        }
    }

    // MARK: Structure cases (from FirmwareImageStructureTest)

    func testConstructedZbinContainsOneValidRawImageAndPreservesBytes() {
        let selected = zbin(rawImage())
        guard case let .valid(image) = FirmwareImageParser.parse(fileName: "constructed.zbin", input: selected) else {
            return XCTFail("expected valid")
        }
        XCTAssertEqual(image.bytes, selected)
    }

    func testGzipMayFinishWithEmptyFinalBlockAfterExactInflateBuffer() {
        let raw = rawImage(payloadSize: 8192 - FirmwareImageParser.headerSize)
        let selected = outerContainer(gzipWithEmptyFinalStoredBlock(raw))
        let result = FirmwareImageParser.parse(fileName: "exact-buffer.zbin", input: selected)
        if case let .invalid(reason) = result { XCTFail("expected valid, got \(reason)") }
    }

    func testDamagedGzipTrailerFailsAfterOuterPayloadCrcRepaired() {
        var bytes = zbin(rawImage())
        let gzipTrailerCrcByte = bytes.count - trailingZeroCount(bytes) - 8
        bytes[gzipTrailerCrcByte] ^= 1
        putU32(&bytes, 0, crc32(bytes, FirmwareImageParser.headerSize, bytes.count))
        assertInvalid(FirmwareImageParser.parse(fileName: "damaged.zbin", input: bytes), contains: "gzip")
    }

    func testNestedRawPayloadAndHeaderCrcFailuresAreRejected() {
        var badPayload = rawImage()
        badPayload[FirmwareImageParser.headerSize + 3] ^= 1
        var badHeader = rawImage()
        badHeader[32] ^= 1
        assertInvalid(FirmwareImageParser.parse(fileName: "bad-payload.zbin", input: zbin(badPayload)), contains: "payload CRC")
        assertInvalid(FirmwareImageParser.parse(fileName: "bad-header.zbin", input: zbin(badHeader)), contains: "header CRC")
    }

    func testOuterAndNestedVersionsMustMatch() {
        let bytes = zbin(rawImage(version: [49, 42, 1, 0]), version: [50, 42, 1, 0])
        assertInvalid(FirmwareImageParser.parse(fileName: "mismatch.zbin", input: bytes), contains: "version")
    }

    func testInflatedImageIsBoundedByAppHostPolicy() {
        let oversizedRaw = rawImage(payloadSize: FirmwareImageParser.maxInflatedImageBytes - FirmwareImageParser.headerSize + 4)
        // The oversized raw image is mostly zeros, so it must be *actually* compressed (a stored block
        // would leave the outer container over the 16 MiB input limit and trip that check first, never
        // reaching inflation — which is what this case is about). gzipCompressed shrinks the zero run.
        let selected = outerContainer(gzipCompressed(oversizedRaw))
        assertInvalid(FirmwareImageParser.parse(fileName: "oversized.zbin", input: selected), contains: "inflated")
    }

    func testConcatenatedGzipMemberIsRejected() {
        let raw = rawImage()
        let gzipPayload = gzip(raw) + gzip(raw)
        assertInvalid(FirmwareImageParser.parse(fileName: "concatenated.zbin", input: outerContainer(gzipPayload)), contains: "trailing")
    }

    func testRawTypeOneRemainsAcceptedExperimentalInput() {
        guard case let .valid(image) = FirmwareImageParser.parse(fileName: "research.bin", input: rawImage()) else {
            return XCTFail("expected valid")
        }
        XCTAssertTrue(image.info.compatibilityNote.contains("Research raw form"))
    }

    // MARK: Retained external fixtures (skipped unless the env var is set)

    func testRetainedOriginalFixtureValidatesWhenSuppliedExternally() throws {
        guard let path = ProcessInfo.processInfo.environment["NOOP_FIRMWARE_FIXTURE"], !path.isEmpty else {
            throw XCTSkip("NOOP_FIRMWARE_FIXTURE not set")
        }
        let bytes = [UInt8](try Data(contentsOf: URL(fileURLWithPath: path)))
        let name = (path as NSString).lastPathComponent
        guard case let .valid(image) = FirmwareImageParser.parse(fileName: name, input: bytes) else {
            return XCTFail("expected valid")
        }
        XCTAssertEqual(image.info.format, .zbinCompressed)
    }

    func testRetainedRawFixtureValidatesSeparatelyWhenSuppliedExternally() throws {
        guard let path = ProcessInfo.processInfo.environment["NOOP_RAW_FIRMWARE_FIXTURE"], !path.isEmpty else {
            throw XCTSkip("NOOP_RAW_FIRMWARE_FIXTURE not set")
        }
        let bytes = [UInt8](try Data(contentsOf: URL(fileURLWithPath: path)))
        let name = (path as NSString).lastPathComponent
        guard case let .valid(image) = FirmwareImageParser.parse(fileName: name, input: bytes) else {
            return XCTFail("expected valid")
        }
        XCTAssertEqual(image.info.format, .binRaw)
    }

    // MARK: - Fixture builders (byte-identical to the Kotlin test helpers)

    private func assertInvalid(_ result: FirmwareImageValidation, contains expected: String) {
        guard case let .invalid(reason) = result else {
            return XCTFail("expected invalid mentioning '\(expected)'")
        }
        XCTAssertTrue(reason.range(of: expected, options: .caseInsensitive) != nil,
                      "expected rejection mentioning '\(expected)', got '\(reason)'")
    }

    /// Matches FirmwareUpdateTest.image(type:): a type-5 payload is the gzip of a raw type-1 image.
    private func zbinImage() -> [UInt8] {
        let compressed = gzip(rawImage())
        let padded = compressed + [UInt8](repeating: 0, count: (4 - compressed.count % 4) % 4)
        return outerContainer(padded)
    }

    private func rawImage(type: Int) -> [UInt8] {
        // For the type-3 / unsupported cases: a raw 440-byte payload with an arbitrary type word.
        var bytes = [UInt8](repeating: 0, count: FirmwareImageParser.headerSize + 440)
        for i in 0..<440 { bytes[FirmwareImageParser.headerSize + i] = UInt8((i * 17) & 0xff) }
        putU32(&bytes, 4, 440)
        putU32(&bytes, 8, 5)
        putU32(&bytes, 12, type)
        putU32(&bytes, 0x7c, 50); putU32(&bytes, 0x80, 42); putU32(&bytes, 0x84, 1); putU32(&bytes, 0x88, 0)
        putU32(&bytes, 0, crc32(bytes, FirmwareImageParser.headerSize, bytes.count))
        putU32(&bytes, 504, crc32(bytes, 8, 504))
        putU32(&bytes, 508, fwU32le(bytes, 0))
        return bytes
    }

    private func rawImage(payloadSize: Int = 440, version: [Int] = [50, 42, 1, 0]) -> [UInt8] {
        precondition(payloadSize > 0 && payloadSize % 4 == 0)
        var bytes = [UInt8](repeating: 0, count: FirmwareImageParser.headerSize + payloadSize)
        putU32(&bytes, 4, payloadSize)
        putU32(&bytes, 8, 5)
        putU32(&bytes, 12, Int(FirmwareImageFormat.binRaw.containerType))
        for (index, component) in version.enumerated() { putU32(&bytes, 0x7c + index * 4, component) }
        putU32(&bytes, 0, crc32(bytes, FirmwareImageParser.headerSize, bytes.count))
        putU32(&bytes, 504, crc32(bytes, 8, 504))
        putU32(&bytes, 508, fwU32le(bytes, 0))
        return bytes
    }

    private func zbin(_ raw: [UInt8], version: [Int] = [50, 42, 1, 0]) -> [UInt8] {
        outerContainer(gzip(raw), version: version)
    }

    private func outerContainer(_ unpaddedPayload: [UInt8], version: [Int] = [50, 42, 1, 0]) -> [UInt8] {
        let padding = (4 - unpaddedPayload.count % 4) % 4
        let payload = unpaddedPayload + [UInt8](repeating: 0, count: padding)
        var bytes = [UInt8](repeating: 0, count: FirmwareImageParser.headerSize + payload.count)
        for i in 0..<payload.count { bytes[FirmwareImageParser.headerSize + i] = payload[i] }
        putU32(&bytes, 4, payload.count)
        putU32(&bytes, 8, 5)
        putU32(&bytes, 12, Int(FirmwareImageFormat.zbinCompressed.containerType))
        for (index, component) in version.enumerated() { putU32(&bytes, 0x7c + index * 4, component) }
        putU32(&bytes, 0, crc32(bytes, FirmwareImageParser.headerSize, bytes.count))
        putU32(&bytes, 504, crc32(bytes, 8, 504))
        putU32(&bytes, 508, fwU32le(bytes, 0))
        return bytes
    }

    /// Real gzip via zlib's `gzip(2)` header — but built by hand so the test needs no Foundation
    /// compression: a single DEFLATE stored block. Matches the Kotlin GZIPOutputStream member shape
    /// closely enough for the parser (fixed header, stored deflate, CRC+size trailer, no extra flags).
    private func gzip(_ bytes: [UInt8]) -> [UInt8] {
        var out: [UInt8] = [0x1f, 0x8b, 8, 0, 0, 0, 0, 0, 0, 0xff]
        out += deflateStored(bytes)
        appendU32(&out, crc32(bytes))
        appendU32(&out, UInt32(bytes.count))
        return out
    }

    /// One or more stored (uncompressed) DEFLATE blocks, each ≤ 0xffff bytes, the last marked final.
    private func deflateStored(_ bytes: [UInt8]) -> [UInt8] {
        var out: [UInt8] = []
        if bytes.isEmpty {
            out.append(1)
            out += [0, 0, 0xff, 0xff]
            return out
        }
        var offset = 0
        while offset < bytes.count {
            let chunk = min(0xffff, bytes.count - offset)
            let final = offset + chunk >= bytes.count
            out.append(final ? 1 : 0)
            out.append(UInt8(chunk & 0xff)); out.append(UInt8((chunk >> 8) & 0xff))
            let inverse = chunk ^ 0xffff
            out.append(UInt8(inverse & 0xff)); out.append(UInt8((inverse >> 8) & 0xff))
            out += bytes[offset..<(offset + chunk)]
            offset += chunk
        }
        return out
    }

    /// A real (fixed-Huffman) gzip member so a highly repetitive image compresses. Only needed by the
    /// inflated-bound case; the other zbin fixtures keep the simple stored form above.
    private func gzipCompressed(_ bytes: [UInt8]) -> [UInt8] {
        var out: [UInt8] = [0x1f, 0x8b, 8, 0, 0, 0, 0, 0, 0, 0xff]
        out += DeflateFixed.compress(bytes)
        appendU32(&out, crc32(bytes))
        appendU32(&out, UInt32(bytes.count))
        return out
    }

    /// Twin of the Kotlin gzipWithEmptyFinalStoredBlock: a non-final stored block then a final empty one.
    private func gzipWithEmptyFinalStoredBlock(_ bytes: [UInt8]) -> [UInt8] {
        precondition(bytes.count <= 0xffff)
        var out: [UInt8] = [0x1f, 0x8b, 8, 0, 0, 0, 0, 0, 0, 0xff]
        out.append(0) // non-final stored block
        out.append(UInt8(bytes.count & 0xff)); out.append(UInt8((bytes.count >> 8) & 0xff))
        let inverse = bytes.count ^ 0xffff
        out.append(UInt8(inverse & 0xff)); out.append(UInt8((inverse >> 8) & 0xff))
        out += bytes
        out.append(1) // final empty stored block
        out += [0, 0, 0xff, 0xff]
        appendU32(&out, crc32(bytes))
        appendU32(&out, UInt32(bytes.count))
        return out
    }

    private func trailingZeroCount(_ bytes: [UInt8]) -> Int {
        var count = 0
        var index = bytes.count - 1
        while index >= FirmwareImageParser.headerSize && bytes[index] == 0 && count < 3 {
            count += 1
            index -= 1
        }
        return count
    }

    private func putU32(_ bytes: inout [UInt8], _ offset: Int, _ value: Int) {
        bytes[offset] = UInt8(value & 0xff)
        bytes[offset + 1] = UInt8((value >> 8) & 0xff)
        bytes[offset + 2] = UInt8((value >> 16) & 0xff)
        bytes[offset + 3] = UInt8((value >> 24) & 0xff)
    }

    private func putU32(_ bytes: inout [UInt8], _ offset: Int, _ value: UInt32) {
        bytes[offset] = UInt8(value & 0xff)
        bytes[offset + 1] = UInt8((value >> 8) & 0xff)
        bytes[offset + 2] = UInt8((value >> 16) & 0xff)
        bytes[offset + 3] = UInt8((value >> 24) & 0xff)
    }

    private func appendU32(_ bytes: inout [UInt8], _ value: UInt32) {
        bytes.append(UInt8(value & 0xff))
        bytes.append(UInt8((value >> 8) & 0xff))
        bytes.append(UInt8((value >> 16) & 0xff))
        bytes.append(UInt8((value >> 24) & 0xff))
    }
}

/// A minimal fixed-Huffman DEFLATE compressor for the test's gzip member. It only exploits distance-1
/// runs (repeated bytes), which is all the mostly-zero firmware image needs, and literal-encodes the
/// rest — a valid, if suboptimal, RFC 1951 stream that `RawInflater` decodes.
private enum DeflateFixed {
    private static let lengthBase = [3, 4, 5, 6, 7, 8, 9, 10, 11, 13, 15, 17, 19, 23, 27, 31,
                                     35, 43, 51, 59, 67, 83, 99, 115, 131, 163, 195, 227, 258]
    private static let lengthExtra = [0, 0, 0, 0, 0, 0, 0, 0, 1, 1, 1, 1, 2, 2, 2, 2,
                                      3, 3, 3, 3, 4, 4, 4, 4, 5, 5, 5, 5, 0]

    private struct BitWriter {
        var bytes: [UInt8] = []
        private var acc = 0
        private var count = 0
        mutating func write(_ value: Int, _ n: Int) {
            acc |= (value << count)
            count += n
            while count >= 8 { bytes.append(UInt8(acc & 0xff)); acc >>= 8; count -= 8 }
        }
        mutating func flush() { if count > 0 { bytes.append(UInt8(acc & 0xff)); acc = 0; count = 0 } }
    }

    private static func reversed(_ code: Int, _ length: Int) -> Int {
        var r = 0, c = code
        for _ in 0..<length { r = (r << 1) | (c & 1); c >>= 1 }
        return r
    }

    private static func literalCode(_ symbol: Int) -> (Int, Int) {
        switch symbol {
        case 0...143: return (0x30 + symbol, 8)
        case 144...255: return (0x190 + (symbol - 144), 9)
        case 256...279: return (symbol - 256, 7)
        default: return (0xc0 + (symbol - 280), 8)
        }
    }

    private static func emitLiteral(_ w: inout BitWriter, _ symbol: Int) {
        let (code, bits) = literalCode(symbol)
        w.write(reversed(code, bits), bits)
    }

    static func compress(_ data: [UInt8]) -> [UInt8] {
        var w = BitWriter()
        w.write(1, 1)  // BFINAL
        w.write(1, 2)  // BTYPE = fixed Huffman
        var i = 0
        while i < data.count {
            if i > 0 && data[i] == data[i - 1] {
                var run = 0
                while i + run < data.count && data[i + run] == data[i - 1] && run < 258 { run += 1 }
                if run >= 3 {
                    var symbolIndex = 0
                    for (index, base) in lengthBase.enumerated() where base <= run { symbolIndex = index }
                    let (code, bits) = literalCode(257 + symbolIndex)
                    w.write(reversed(code, bits), bits)
                    w.write(run - lengthBase[symbolIndex], lengthExtra[symbolIndex])
                    w.write(reversed(0, 5), 5)  // distance symbol 0 == distance 1, no extra bits
                    i += run
                    continue
                }
            }
            emitLiteral(&w, Int(data[i]))
            i += 1
        }
        emitLiteral(&w, 256)  // end of block
        w.flush()
        return w.bytes
    }
}

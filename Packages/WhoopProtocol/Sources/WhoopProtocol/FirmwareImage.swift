import Foundation

// Local validation of a WHOOP 5/MG update container selected in Test Centre. Twin of the Kotlin
// `FirmwareImageParser` (android/.../ble/FirmwareUpdate.kt): the same checks run in the same order and
// fail with the same messages, so a rejected image reads identically on both platforms.
//
// The CRC32 here is CRC-32/ISO-HDLC, the zlib `crc32` this package already uses for frames. A CRC match
// is integrity evidence only; it says nothing about signatures, compatibility, or a successful boot.

/// The two container types whose layout and CRC predicates are retained from the strap firmware.
public enum FirmwareImageFormat: CaseIterable, Sendable {
    case binRaw
    case zbinCompressed

    /// The type word at header offset 12.
    public var containerType: UInt32 {
        switch self {
        case .binRaw: return 1
        case .zbinCompressed: return 5
        }
    }

    public var displayName: String {
        switch self {
        case .binRaw: return "Raw BIN (experimental)"
        case .zbinCompressed: return "Compressed ZBIN (OTA format)"
        }
    }
}

public struct FirmwareImageInfo: Equatable, Sendable {
    public let fileName: String
    public let byteCount: Int
    public let format: FirmwareImageFormat
    public let version: String
    public let payloadLength: Int
    public let payloadCrc32: String
    public let headerCrc32: String
    public let sha256: String
    public let compatibilityNote: String

    public init(fileName: String, byteCount: Int, format: FirmwareImageFormat, version: String,
                payloadLength: Int, payloadCrc32: String, headerCrc32: String, sha256: String,
                compatibilityNote: String) {
        self.fileName = fileName
        self.byteCount = byteCount
        self.format = format
        self.version = version
        self.payloadLength = payloadLength
        self.payloadCrc32 = payloadCrc32
        self.headerCrc32 = headerCrc32
        self.sha256 = sha256
        self.compatibilityNote = compatibilityNote
    }
}

/// A container that passed every local check. `bytes` is the session's own immutable copy and is what
/// goes on the wire, unchanged from byte 0 through EOF.
public struct ValidatedFirmwareImage: Sendable {
    public let info: FirmwareImageInfo
    public let bytes: [UInt8]
}

public enum FirmwareImageValidation: Sendable {
    case valid(ValidatedFirmwareImage)
    case invalid(String)
}

/// Fail-closed parser for the 512-byte WHOOP update container.
public enum FirmwareImageParser {
    public static let headerSize = 512
    public static let chunkSize = 220
    /// App memory/input policy, not a device capacity claim.
    public static let maxImageBytes = 16 * 1024 * 1024
    /// Host memory policy, not proven strap capacity.
    public static let maxInflatedImageBytes = 16 * 1024 * 1024

    private static let gzipFixedHeaderSize = 10
    private static let gzipTrailerSize = 8
    private static let gzipFlagHeaderCrc: UInt8 = 0x02
    private static let gzipFlagExtra: UInt8 = 0x04
    private static let gzipFlagName: UInt8 = 0x08
    private static let gzipFlagComment: UInt8 = 0x10
    private static let gzipReservedFlags: UInt8 = 0xe0

    /// Kotlin twin: `FirmwareImageParser.parse`.
    public static func parse(fileName: String, input: [UInt8]) -> FirmwareImageValidation {
        let ext: String
        if let dot = fileName.lastIndex(of: ".") {
            ext = String(fileName[fileName.index(after: dot)...]).lowercased()
        } else {
            ext = ""
        }
        if ext != "zbin" && ext != "bin" {
            return .invalid("Choose a .zbin or .bin firmware container")
        }
        if input.count < headerSize + 4 {
            return .invalid("Image is shorter than the 512-byte header and payload")
        }
        if input.count > maxImageBytes {
            return .invalid("Image exceeds the app's 16 MiB safety limit")
        }
        if input.count % 4 != 0 {
            return .invalid("Image length must be a multiple of 4 bytes")
        }

        let declaredPayload = fwU32le(input, 4)
        if declaredPayload > UInt32(Int32.max) {
            return .invalid("Declared payload length is too large")
        }
        let payloadLength = Int(declaredPayload)
        if payloadLength <= 0 || payloadLength != input.count - headerSize {
            return .invalid("Declared payload length \(payloadLength) does not match \(input.count - headerSize) bytes")
        }
        if payloadLength % 4 != 0 {
            return .invalid("Payload length must be a multiple of 4 bytes")
        }

        let type = fwU32le(input, 12)
        guard let format = FirmwareImageFormat.allCases.first(where: { $0.containerType == type }) else {
            return .invalid("Unsupported firmware container type \(type)")
        }
        let expectedExtension = format == .zbinCompressed ? "zbin" : "bin"
        if ext != expectedExtension {
            return .invalid("The .\(ext) name does not match the container type (\(format.displayName))")
        }

        let storedPayloadCrc = fwU32le(input, 0)
        let computedPayloadCrc = crc32(input, headerSize, input.count)
        if storedPayloadCrc != computedPayloadCrc {
            return .invalid("Payload CRC mismatch: stored \(fwHex8(storedPayloadCrc)), computed \(fwHex8(computedPayloadCrc))")
        }
        let storedHeaderCrc = fwU32le(input, 504)
        let computedHeaderCrc = crc32(input, 8, 504)
        if storedHeaderCrc != computedHeaderCrc {
            return .invalid("Header CRC mismatch: stored \(fwHex8(storedHeaderCrc)), computed \(fwHex8(computedHeaderCrc))")
        }

        let version = versionString(input)
        if format == .zbinCompressed {
            let inflated: [UInt8]
            switch inflateSingleGzipMember(input) {
            case .success(let bytes): inflated = bytes
            case .failure(let failure): return .invalid(failure.reason)
            }
            if let reason = validateNestedRawImage(inflated, outerVersion: version) {
                return .invalid(reason)
            }
        }
        let note: String
        switch format {
        case .zbinCompressed:
            note = "Vendor OTA container with a structurally valid nested raw image. CRCs do not prove device compatibility or authentication."
        case .binRaw:
            note = "Research raw form. Installation of this decompressed type is not established."
        }
        let info = FirmwareImageInfo(
            fileName: fileName,
            byteCount: input.count,
            format: format,
            version: version,
            payloadLength: payloadLength,
            payloadCrc32: fwHex8(storedPayloadCrc),
            headerCrc32: fwHex8(storedHeaderCrc),
            sha256: FirmwareSha256.digest(input).hexLower,
            compatibilityNote: note
        )
        return .valid(ValidatedFirmwareImage(info: info, bytes: input))
    }

    private struct InflationFailure: Error {
        let reason: String
    }

    /// Exactly one gzip member (RFC 1952) after the 512-byte header, followed by at most three zero
    /// alignment bytes. Everything the member declares is checked: header flags and optional fields,
    /// the optional header CRC, the deflate stream itself, the trailer CRC and the trailer size.
    /// Kotlin twin: `FirmwareImageParser.inflateSingleGzipMember`.
    private static func inflateSingleGzipMember(_ container: [UInt8]) -> Result<[UInt8], InflationFailure> {
        func fail(_ reason: String) -> Result<[UInt8], InflationFailure> { .failure(InflationFailure(reason: reason)) }
        let start = headerSize
        let end = container.count
        if end - start < gzipFixedHeaderSize + gzipTrailerSize {
            return fail("Compressed payload is too short to contain a complete gzip member")
        }
        if container[start] != 0x1f || container[start + 1] != 0x8b {
            return fail("Compressed payload does not start with a gzip header")
        }
        if container[start + 2] != 8 {
            return fail("Compressed payload uses an unsupported gzip compression method")
        }

        let flags = container[start + 3]
        if flags & gzipReservedFlags != 0 {
            return fail("Compressed payload has invalid reserved gzip flags")
        }
        var cursor = start + gzipFixedHeaderSize

        // Kotlin twin: `FirmwareImageParser.requireAvailable`.
        func truncation(_ count: Int, _ part: String) -> String? {
            (count < 0 || cursor > end - count) ? "Compressed payload has a truncated gzip \(part)" : nil
        }

        if flags & gzipFlagExtra != 0 {
            if let reason = truncation(2, "extra-field length") { return fail(reason) }
            let extraLength = fwU16le(container, cursor)
            cursor += 2
            if let reason = truncation(extraLength, "extra field") { return fail(reason) }
            cursor += extraLength
        }
        if flags & gzipFlagName != 0 {
            guard let next = zeroTerminator(container, from: cursor, until: end) else {
                return fail("Compressed payload has a truncated gzip file name")
            }
            cursor = next
        }
        if flags & gzipFlagComment != 0 {
            guard let next = zeroTerminator(container, from: cursor, until: end) else {
                return fail("Compressed payload has a truncated gzip comment")
            }
            cursor = next
        }
        if flags & gzipFlagHeaderCrc != 0 {
            if let reason = truncation(2, "header CRC") { return fail(reason) }
            let storedHeaderCrc = fwU16le(container, cursor)
            let computedHeaderCrc = Int(crc32(container, start, cursor) & 0xffff)
            if storedHeaderCrc != computedHeaderCrc {
                return fail("Compressed payload gzip header CRC mismatch")
            }
            cursor += 2
        }
        if cursor > end - gzipTrailerSize {
            return fail("Compressed payload has no complete gzip data and trailer")
        }

        var inflater = RawInflater(input: container, start: cursor, end: end, limit: maxInflatedImageBytes)
        do {
            try inflater.run()
        } catch RawInflater.Failure.limit {
            return fail("Compressed payload exceeds the app's \(maxInflatedImageBytes)-byte inflated host-policy limit")
        } catch RawInflater.Failure.truncated {
            return fail("Compressed payload has a truncated gzip deflate stream")
        } catch {
            return fail("Compressed payload contains invalid gzip deflate data")
        }
        let inflated = inflater.output

        let trailerStart = inflater.consumedEnd
        if trailerStart > end - gzipTrailerSize {
            return fail("Compressed payload has a truncated gzip trailer")
        }
        let storedInflatedCrc = fwU32le(container, trailerStart)
        let computedInflatedCrc = crc32(inflated)
        if storedInflatedCrc != computedInflatedCrc {
            return fail("Compressed payload gzip CRC mismatch: stored \(fwHex8(storedInflatedCrc)), computed \(fwHex8(computedInflatedCrc))")
        }
        let storedInflatedSize = fwU32le(container, trailerStart + 4)
        if UInt64(storedInflatedSize) != UInt64(inflated.count) {
            return fail("Compressed payload gzip size mismatch: stored \(storedInflatedSize), inflated \(inflated.count)")
        }

        let trailingStart = trailerStart + gzipTrailerSize
        let trailingCount = end - trailingStart
        if !(0...3).contains(trailingCount) || container[trailingStart..<end].contains(where: { $0 != 0 }) {
            return fail("Compressed payload has trailing data after its single gzip member")
        }
        return .success(inflated)
    }

    /// Kotlin twin: `FirmwareImageParser.validateNestedRawImage`.
    private static func validateNestedRawImage(_ raw: [UInt8], outerVersion: String) -> String? {
        if raw.count < headerSize + 4 {
            return "Nested raw image is shorter than the 512-byte header and payload"
        }
        if raw.count % 4 != 0 { return "Nested raw image length must be a multiple of 4 bytes" }

        let declaredPayload = fwU32le(raw, 4)
        if declaredPayload > UInt32(Int32.max) { return "Nested raw declared payload length is too large" }
        let payloadLength = Int(declaredPayload)
        if payloadLength <= 0 || payloadLength != raw.count - headerSize {
            return "Nested raw declared payload length \(payloadLength) does not match \(raw.count - headerSize) bytes"
        }
        if payloadLength % 4 != 0 { return "Nested raw payload length must be a multiple of 4 bytes" }
        if fwU32le(raw, 12) != FirmwareImageFormat.binRaw.containerType {
            return "Nested firmware container must have raw type 1"
        }

        let storedPayloadCrc = fwU32le(raw, 0)
        let computedPayloadCrc = crc32(raw, headerSize, raw.count)
        if storedPayloadCrc != computedPayloadCrc {
            return "Nested raw payload CRC mismatch: stored \(fwHex8(storedPayloadCrc)), computed \(fwHex8(computedPayloadCrc))"
        }
        let storedHeaderCrc = fwU32le(raw, 504)
        let computedHeaderCrc = crc32(raw, 8, 504)
        if storedHeaderCrc != computedHeaderCrc {
            return "Nested raw header CRC mismatch: stored \(fwHex8(storedHeaderCrc)), computed \(fwHex8(computedHeaderCrc))"
        }

        let nestedVersion = versionString(raw)
        if nestedVersion != outerVersion {
            return "Nested raw version \(nestedVersion) does not match outer version \(outerVersion)"
        }
        return nil
    }

    /// Kotlin twin: `ByteArray.findGzipZeroTerminator`.
    private static func zeroTerminator(_ bytes: [UInt8], from: Int, until: Int) -> Int? {
        var index = from
        while index < until {
            if bytes[index] == 0 { return index + 1 }
            index += 1
        }
        return nil
    }

    /// The four version words at 0x7c/0x80/0x84/0x88, printed as unsigned decimals joined by dots.
    /// Kotlin twin: `ByteArray.versionString`.
    private static func versionString(_ bytes: [UInt8]) -> String {
        [0x7c, 0x80, 0x84, 0x88].map { String(fwU32le(bytes, $0)) }.joined(separator: ".")
    }
}

// MARK: - Byte helpers (file-local names so they cannot collide with Framing.swift's private twins)

/// Kotlin twin: `ByteArray.u32le`.
@inline(__always)
func fwU32le(_ bytes: [UInt8], _ offset: Int) -> UInt32 {
    UInt32(bytes[offset]) | (UInt32(bytes[offset + 1]) << 8)
        | (UInt32(bytes[offset + 2]) << 16) | (UInt32(bytes[offset + 3]) << 24)
}

/// Kotlin twin: `ByteArray.u16le`.
@inline(__always)
func fwU16le(_ bytes: [UInt8], _ offset: Int) -> Int {
    Int(bytes[offset]) | (Int(bytes[offset + 1]) << 8)
}

/// `%08x`: eight lowercase hex digits, zero padded.
/// Kotlin twin: `Long.hex8`.
func fwHex8(_ value: UInt32) -> String {
    let digits = String(value, radix: 16)
    return String(repeating: "0", count: max(0, 8 - digits.count)) + digits
}

// MARK: - Raw DEFLATE (RFC 1951)

/// Bounded raw-DEFLATE decoder for the one gzip member inside a type-5 container. Foundation offers no
/// inflate on Linux, and the package takes no dependencies, so this is a small canonical-Huffman decoder.
/// It keeps the acceptance rules the Kotlin twin inherits from zlib: the code-length code must be
/// complete, a literal/length or distance code may be incomplete only when it is a single one-bit code,
/// the end-of-block symbol must have a code, and a distance may never reach before the first output byte.
struct RawInflater {
    enum Failure: Error {
        case truncated
        case invalid
        case limit
    }

    private let input: [UInt8]
    private var position: Int
    private let end: Int
    private let limit: Int
    private var bitBuffer: UInt32 = 0
    private var bitCount = 0
    private(set) var output: [UInt8] = []

    /// One past the last input byte the deflate stream used (a partially used final byte counts as used).
    var consumedEnd: Int { position }

    init(input: [UInt8], start: Int, end: Int, limit: Int) {
        self.input = input
        self.position = start
        self.end = end
        self.limit = limit
        output.reserveCapacity(min(64 * 1024, limit))
    }

    private static let lengthBase: [Int] = [3, 4, 5, 6, 7, 8, 9, 10, 11, 13, 15, 17, 19, 23, 27, 31,
                                            35, 43, 51, 59, 67, 83, 99, 115, 131, 163, 195, 227, 258]
    private static let lengthExtra: [Int] = [0, 0, 0, 0, 0, 0, 0, 0, 1, 1, 1, 1, 2, 2, 2, 2,
                                             3, 3, 3, 3, 4, 4, 4, 4, 5, 5, 5, 5, 0]
    private static let distanceBase: [Int] = [1, 2, 3, 4, 5, 7, 9, 13, 17, 25, 33, 49, 65, 97, 129, 193,
                                              257, 385, 513, 769, 1025, 1537, 2049, 3073, 4097, 6145,
                                              8193, 12289, 16385, 24577]
    private static let distanceExtra: [Int] = [0, 0, 0, 0, 1, 1, 2, 2, 3, 3, 4, 4, 5, 5, 6, 6,
                                               7, 7, 8, 8, 9, 9, 10, 10, 11, 11, 12, 12, 13, 13]
    private static let codeLengthOrder: [Int] = [16, 17, 18, 0, 8, 7, 9, 6, 10, 5, 11, 4, 12, 3, 13, 2, 14, 1, 15]
    private static let maxBits = 15

    private struct Huffman {
        var count = [Int](repeating: 0, count: RawInflater.maxBits + 1)
        var symbol: [Int]

        /// Returns the table and the number of unused codes: 0 = complete, > 0 = incomplete,
        /// < 0 = over-subscribed.
        static func build(_ lengths: ArraySlice<Int>) -> (Huffman, Int) {
            var h = Huffman(symbol: [Int](repeating: 0, count: lengths.count))
            for length in lengths { h.count[length] += 1 }
            if h.count[0] == lengths.count { return (h, 0) }
            var left = 1
            for length in 1...RawInflater.maxBits {
                left <<= 1
                left -= h.count[length]
                if left < 0 { return (h, left) }
            }
            var offsets = [Int](repeating: 0, count: RawInflater.maxBits + 1)
            for length in 1..<RawInflater.maxBits {
                offsets[length + 1] = offsets[length] + h.count[length]
            }
            for (index, length) in lengths.enumerated() where length != 0 {
                h.symbol[offsets[length]] = index
                offsets[length] += 1
            }
            return (h, left)
        }
    }

    mutating func run() throws {
        var last = false
        while !last {
            last = try bits(1) == 1
            switch try bits(2) {
            case 0: try stored()
            case 1: try codes(literal: RawInflater.fixedLiteral, distance: RawInflater.fixedDistance)
            case 2: try dynamic()
            default: throw Failure.invalid
            }
        }
    }

    private mutating func bits(_ need: Int) throws -> Int {
        var value = bitBuffer
        while bitCount < need {
            guard position < end else { throw Failure.truncated }
            value |= UInt32(input[position]) << UInt32(bitCount)
            position += 1
            bitCount += 8
        }
        bitBuffer = need == 32 ? 0 : value >> UInt32(need)
        bitCount -= need
        return Int(value & ((UInt32(1) << UInt32(need)) &- 1))
    }

    private mutating func append(_ byte: UInt8) throws {
        guard output.count < limit else { throw Failure.limit }
        output.append(byte)
    }

    private mutating func stored() throws {
        bitBuffer = 0
        bitCount = 0
        guard position + 4 <= end else { throw Failure.truncated }
        let length = Int(input[position]) | (Int(input[position + 1]) << 8)
        let inverse = Int(input[position + 2]) | (Int(input[position + 3]) << 8)
        position += 4
        guard length == (~inverse & 0xffff) else { throw Failure.invalid }
        guard position + length <= end else { throw Failure.truncated }
        for index in position..<(position + length) { try append(input[index]) }
        position += length
    }

    private mutating func decode(_ h: Huffman) throws -> Int {
        var code = 0
        var first = 0
        var index = 0
        for length in 1...RawInflater.maxBits {
            code |= try bits(1)
            let count = h.count[length]
            if code - count < first { return h.symbol[index + (code - first)] }
            index += count
            first += count
            first <<= 1
            code <<= 1
        }
        throw Failure.invalid
    }

    private mutating func codes(literal: Huffman, distance: Huffman) throws {
        while true {
            var symbol = try decode(literal)
            if symbol < 256 {
                try append(UInt8(symbol))
            } else if symbol == 256 {
                return
            } else {
                symbol -= 257
                guard symbol < 29 else { throw Failure.invalid }
                let length = RawInflater.lengthBase[symbol] + (try bits(RawInflater.lengthExtra[symbol]))
                let distanceSymbol = try decode(distance)
                guard distanceSymbol < 30 else { throw Failure.invalid }
                let back = RawInflater.distanceBase[distanceSymbol] + (try bits(RawInflater.distanceExtra[distanceSymbol]))
                guard back <= output.count else { throw Failure.invalid }
                for _ in 0..<length { try append(output[output.count - back]) }
            }
        }
    }

    private mutating func dynamic() throws {
        let literalCount = try bits(5) + 257
        let distanceCount = try bits(5) + 1
        let codeCount = try bits(4) + 4
        guard literalCount <= 286, distanceCount <= 30 else { throw Failure.invalid }

        var lengths = [Int](repeating: 0, count: 19)
        for index in 0..<codeCount { lengths[RawInflater.codeLengthOrder[index]] = try bits(3) }
        let (lengthCode, lengthLeft) = Huffman.build(lengths[...])
        guard lengthLeft == 0 else { throw Failure.invalid }

        let total = literalCount + distanceCount
        lengths = [Int](repeating: 0, count: total)
        var index = 0
        while index < total {
            var symbol = try decode(lengthCode)
            if symbol < 16 {
                lengths[index] = symbol
                index += 1
                continue
            }
            var repeated = 0
            if symbol == 16 {
                guard index > 0 else { throw Failure.invalid }
                repeated = lengths[index - 1]
                symbol = 3 + (try bits(2))
            } else if symbol == 17 {
                symbol = 3 + (try bits(3))
            } else {
                symbol = 11 + (try bits(7))
            }
            guard index + symbol <= total else { throw Failure.invalid }
            for _ in 0..<symbol {
                lengths[index] = repeated
                index += 1
            }
        }
        guard lengths[256] != 0 else { throw Failure.invalid }

        let (literal, literalLeft) = Huffman.build(lengths[0..<literalCount])
        if literalLeft != 0 && (literalLeft < 0 || literalCount != literal.count[0] + literal.count[1]) {
            throw Failure.invalid
        }
        let (distance, distanceLeft) = Huffman.build(lengths[literalCount..<total])
        if distanceLeft != 0 && (distanceLeft < 0 || distanceCount != distance.count[0] + distance.count[1]) {
            throw Failure.invalid
        }
        try codes(literal: literal, distance: distance)
    }

    private static let fixedLiteral: Huffman = {
        var lengths = [Int](repeating: 8, count: 288)
        for i in 144..<256 { lengths[i] = 9 }
        for i in 256..<280 { lengths[i] = 7 }
        return Huffman.build(lengths[0..<288]).0
    }()

    private static let fixedDistance: Huffman = {
        Huffman.build([Int](repeating: 5, count: 30)[0..<30]).0
    }()
}

// MARK: - SHA-256 (FIPS 180-4)

/// SHA-256 for the image identity shown in Test Centre and bound into a paused session. CryptoKit is
/// Apple-only and this package builds on Linux without dependencies, so the digest is computed here.
enum FirmwareSha256 {
    private static let k: [UInt32] = [
        0x428a2f98, 0x71374491, 0xb5c0fbcf, 0xe9b5dba5, 0x3956c25b, 0x59f111f1, 0x923f82a4, 0xab1c5ed5,
        0xd807aa98, 0x12835b01, 0x243185be, 0x550c7dc3, 0x72be5d74, 0x80deb1fe, 0x9bdc06a7, 0xc19bf174,
        0xe49b69c1, 0xefbe4786, 0x0fc19dc6, 0x240ca1cc, 0x2de92c6f, 0x4a7484aa, 0x5cb0a9dc, 0x76f988da,
        0x983e5152, 0xa831c66d, 0xb00327c8, 0xbf597fc7, 0xc6e00bf3, 0xd5a79147, 0x06ca6351, 0x14292967,
        0x27b70a85, 0x2e1b2138, 0x4d2c6dfc, 0x53380d13, 0x650a7354, 0x766a0abb, 0x81c2c92e, 0x92722c85,
        0xa2bfe8a1, 0xa81a664b, 0xc24b8b70, 0xc76c51a3, 0xd192e819, 0xd6990624, 0xf40e3585, 0x106aa070,
        0x19a4c116, 0x1e376c08, 0x2748774c, 0x34b0bcb5, 0x391c0cb3, 0x4ed8aa4a, 0x5b9cca4f, 0x682e6ff3,
        0x748f82ee, 0x78a5636f, 0x84c87814, 0x8cc70208, 0x90befffa, 0xa4506ceb, 0xbef9a3f7, 0xc67178f2,
    ]

    static func digest(_ message: [UInt8]) -> [UInt8] {
        var h: [UInt32] = [0x6a09e667, 0xbb67ae85, 0x3c6ef372, 0xa54ff53a,
                           0x510e527f, 0x9b05688c, 0x1f83d9ab, 0x5be0cd19]
        var padded = message
        padded.append(0x80)
        while padded.count % 64 != 56 { padded.append(0) }
        let bitLength = UInt64(message.count) &* 8
        for shift in stride(from: 56, through: 0, by: -8) { padded.append(UInt8(truncatingIfNeeded: bitLength >> UInt64(shift))) }

        var w = [UInt32](repeating: 0, count: 64)
        var block = 0
        while block < padded.count {
            for t in 0..<16 {
                let i = block + t * 4
                w[t] = (UInt32(padded[i]) << 24) | (UInt32(padded[i + 1]) << 16)
                    | (UInt32(padded[i + 2]) << 8) | UInt32(padded[i + 3])
            }
            for t in 16..<64 {
                let s0 = rotr(w[t - 15], 7) ^ rotr(w[t - 15], 18) ^ (w[t - 15] >> 3)
                let s1 = rotr(w[t - 2], 17) ^ rotr(w[t - 2], 19) ^ (w[t - 2] >> 10)
                w[t] = w[t - 16] &+ s0 &+ w[t - 7] &+ s1
            }
            var a = h[0], b = h[1], c = h[2], d = h[3], e = h[4], f = h[5], g = h[6], hh = h[7]
            for t in 0..<64 {
                let s1 = rotr(e, 6) ^ rotr(e, 11) ^ rotr(e, 25)
                let ch = (e & f) ^ (~e & g)
                let t1 = hh &+ s1 &+ ch &+ k[t] &+ w[t]
                let s0 = rotr(a, 2) ^ rotr(a, 13) ^ rotr(a, 22)
                let maj = (a & b) ^ (a & c) ^ (b & c)
                let t2 = s0 &+ maj
                hh = g; g = f; f = e; e = d &+ t1
                d = c; c = b; b = a; a = t1 &+ t2
            }
            h[0] = h[0] &+ a; h[1] = h[1] &+ b; h[2] = h[2] &+ c; h[3] = h[3] &+ d
            h[4] = h[4] &+ e; h[5] = h[5] &+ f; h[6] = h[6] &+ g; h[7] = h[7] &+ hh
            block += 64
        }
        var out: [UInt8] = []
        out.reserveCapacity(32)
        for word in h {
            out.append(UInt8(truncatingIfNeeded: word >> 24))
            out.append(UInt8(truncatingIfNeeded: word >> 16))
            out.append(UInt8(truncatingIfNeeded: word >> 8))
            out.append(UInt8(truncatingIfNeeded: word))
        }
        return out
    }

    @inline(__always)
    private static func rotr(_ x: UInt32, _ n: UInt32) -> UInt32 { (x >> n) | (x << (32 - n)) }
}

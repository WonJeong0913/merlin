import Foundation

enum BridgeCommand: String, Codable, Sendable {
    case hello = "bridge.hello"
    case accountStatus = "account.status"
    case accountConnectSpec = "account.connect_spec"
    case accountModels = "account.models"
    case sessionStart = "session.start"
    case sessionRestart = "session.restart"
    case sessionUpdateSettings = "session.update_settings"
    case sessionStatus = "session.status"
    case sessionNewThread = "session.new_thread"
    case sessionResumeThread = "session.resume_thread"
    case chatSend = "chat.send"
    case approvalResolve = "approval.resolve"
    case feedbackRecord = "feedback.record"
    case harnessGovernance = "harness.governance"
}

enum BridgeValue: Codable, Equatable, Sendable {
    case string(String)
    case number(Double)
    case bool(Bool)
    case object([String: BridgeValue])
    case array([BridgeValue])
    case null

    init(from decoder: Decoder) throws {
        let container = try decoder.singleValueContainer()
        if container.decodeNil() { self = .null }
        else if let value = try? container.decode(Bool.self) { self = .bool(value) }
        else if let value = try? container.decode(Double.self) { self = .number(value) }
        else if let value = try? container.decode(String.self) { self = .string(value) }
        else if let value = try? container.decode([String: BridgeValue].self) { self = .object(value) }
        else if let value = try? container.decode([BridgeValue].self) { self = .array(value) }
        else { throw DecodingError.dataCorruptedError(in: container, debugDescription: "Unsupported bridge JSON value") }
    }

    func encode(to encoder: Encoder) throws {
        var container = encoder.singleValueContainer()
        switch self {
        case let .string(value): try container.encode(value)
        case let .number(value): try container.encode(value)
        case let .bool(value): try container.encode(value)
        case let .object(value): try container.encode(value)
        case let .array(value): try container.encode(value)
        case .null: try container.encodeNil()
        }
    }

    var string: String? {
        guard case let .string(value) = self else { return nil }
        return value
    }

    var bool: Bool? {
        guard case let .bool(value) = self else { return nil }
        return value
    }

    var int: Int? {
        guard case let .number(value) = self else { return nil }
        return Int(exactly: value)
    }

    var double: Double? {
        guard case let .number(value) = self else { return nil }
        return value
    }

    var object: [String: BridgeValue]? {
        guard case let .object(value) = self else { return nil }
        return value
    }

    var array: [BridgeValue]? {
        guard case let .array(value) = self else { return nil }
        return value
    }
}

struct BridgeRequest: Codable, Sendable {
    let requestID: String
    let command: BridgeCommand
    let payload: [String: BridgeValue]

    enum CodingKeys: String, CodingKey {
        case requestID = "request_id"
        case command
        case payload
    }
}

struct BridgeResponse: Codable, Equatable, Sendable {
    let schemaVersion: Int
    let requestID: String?
    let ok: Bool
    let event: String
    let data: [String: BridgeValue]?
    let error: String?

    enum CodingKeys: String, CodingKey {
        case schemaVersion = "schema_version"
        case requestID = "request_id"
        case ok
        case event
        case data
        case error
    }
}

enum BridgeProtocolError: LocalizedError, Equatable {
    case malformedResponse
    case responseTooLarge
    case requestIDMismatch(expected: String, actual: String?)
    case missingData
    case unsupportedEvent(String)
    case invalidData(String)

    var errorDescription: String? {
        switch self {
        case .malformedResponse: "The local bridge returned an unreadable response."
        case .responseTooLarge: "The local bridge response exceeded the safe display limit."
        case let .requestIDMismatch(expected, actual): "The local bridge response did not match request \(expected) (received \(actual ?? "none"))."
        case .missingData: "The local bridge response did not include its expected data."
        case let .unsupportedEvent(event): "The local bridge returned unsupported event \(event)."
        case let .invalidData(reason): "The local bridge returned invalid data: \(reason)"
        }
    }
}

enum BridgeResponseDecoder {
    static let maxResponseLineBytes = 1_048_576

    static func decode(_ line: Data, expectedRequestID: String) throws -> BridgeResponse {
        guard line.count <= maxResponseLineBytes else { throw BridgeProtocolError.responseTooLarge }
        let response: BridgeResponse
        do {
            response = try JSONDecoder().decode(BridgeResponse.self, from: line)
        } catch {
            throw BridgeProtocolError.malformedResponse
        }
        guard response.schemaVersion == 1 else { throw BridgeProtocolError.malformedResponse }
        guard response.requestID == expectedRequestID else {
            throw BridgeProtocolError.requestIDMismatch(expected: expectedRequestID, actual: response.requestID)
        }
        return response
    }
}

protocol BridgeTransport: Sendable {
    func request(command: BridgeCommand, payload: [String: BridgeValue]) async throws -> BridgeResponse
    func shutdown() async
}

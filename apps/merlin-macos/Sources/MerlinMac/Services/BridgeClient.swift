import Foundation

struct BridgeLaunchConfiguration: Sendable {
    let repositoryRoot: URL?
    let pythonExecutable: String

    init(repositoryRoot: URL? = nil, pythonExecutable: String = "python3") {
        self.repositoryRoot = repositoryRoot
        self.pythonExecutable = pythonExecutable
    }
}

enum BridgeClientError: LocalizedError {
    case repositoryNotFound
    case launchFailed(String)
    case bridgeTerminated
    case inputUnavailable

    var errorDescription: String? {
        switch self {
        case .repositoryNotFound:
            "Merlin repository was not found. Set MERLIN_REPO_ROOT before launching the desktop app."
        case let .launchFailed(reason):
            "The local bridge could not start: \(reason)"
        case .bridgeTerminated:
            "The local bridge stopped before returning a response."
        case .inputUnavailable:
            "The local bridge input is unavailable."
        }
    }
}

/// A single-flight client for the bridge's one-request/one-response JSONL contract.
/// stderr is deliberately discarded: UI code consumes only schema-validated stdout.
actor BridgeClient: BridgeTransport {
    private let configuration: BridgeLaunchConfiguration
    private var process: Process?
    private var input: FileHandle?
    private var output: FileHandle?
    private var unreadOutput = Data()

    init(configuration: BridgeLaunchConfiguration = BridgeLaunchConfiguration()) {
        self.configuration = configuration
    }

    func request(command: BridgeCommand, payload: [String: BridgeValue]) async throws -> BridgeResponse {
        try launchIfNeeded()
        guard let input else { throw BridgeClientError.inputUnavailable }

        let requestID = UUID().uuidString.lowercased()
        let request = BridgeRequest(requestID: requestID, command: command, payload: payload)
        var encoded = try JSONEncoder().encode(request)
        encoded.append(0x0A)
        do {
            try input.write(contentsOf: encoded)
        } catch {
            throw BridgeClientError.inputUnavailable
        }

        let line = try await readResponseLine()
        return try BridgeResponseDecoder.decode(line, expectedRequestID: requestID)
    }

    func shutdown() async {
        input?.closeFile()
        output?.closeFile()
        if let process, process.isRunning { process.terminate() }
        input = nil
        output = nil
        process = nil
        unreadOutput = Data()
    }

    private func launchIfNeeded() throws {
        if let process, process.isRunning { return }
        cleanupExitedProcess()

        let root = try resolveRepositoryRoot()
        let bridgePath = root.appendingPathComponent("bridge/merlin_bridge.py")
        guard FileManager.default.isExecutableFile(atPath: "/usr/bin/env"),
              FileManager.default.fileExists(atPath: bridgePath.path) else {
            throw BridgeClientError.repositoryNotFound
        }

        let nextProcess = Process()
        let stdin = Pipe()
        let stdout = Pipe()
        nextProcess.executableURL = URL(fileURLWithPath: "/usr/bin/env")
        nextProcess.arguments = [configuration.pythonExecutable, bridgePath.path]
        nextProcess.currentDirectoryURL = root
        var environment = ProcessInfo.processInfo.environment
        let inheritedPythonPath = environment["PYTHONPATH"]
        environment["PYTHONPATH"] = inheritedPythonPath.map { "\(root.path):\($0)" } ?? root.path
        nextProcess.environment = environment
        nextProcess.standardInput = stdin
        nextProcess.standardOutput = stdout
        nextProcess.standardError = FileHandle.nullDevice

        do {
            try nextProcess.run()
        } catch {
            throw BridgeClientError.launchFailed(error.localizedDescription)
        }
        process = nextProcess
        input = stdin.fileHandleForWriting
        output = stdout.fileHandleForReading
        unreadOutput = Data()
    }

    private func cleanupExitedProcess() {
        input?.closeFile()
        output?.closeFile()
        input = nil
        output = nil
        process = nil
        unreadOutput = Data()
    }

    private func resolveRepositoryRoot() throws -> URL {
        if let configured = configuration.repositoryRoot, Self.isRepositoryRoot(configured) {
            return configured
        }
        if let path = ProcessInfo.processInfo.environment["MERLIN_REPO_ROOT"],
           Self.isRepositoryRoot(URL(fileURLWithPath: path)) {
            return URL(fileURLWithPath: path)
        }
        // `run-app.sh` embeds the checkout path in the temporary bundle so a
        // Finder/LaunchServices launch can resolve the local bridge without
        // depending on a shell environment that disappears after launch.
        if let rootPointer = Bundle.main.url(forResource: "repository-root", withExtension: "txt"),
           let rawPath = try? String(contentsOf: rootPointer, encoding: .utf8) {
            let path = rawPath.trimmingCharacters(in: .whitespacesAndNewlines)
            if Self.isRepositoryRoot(URL(fileURLWithPath: path)) {
                return URL(fileURLWithPath: path)
            }
        }
        let current = URL(fileURLWithPath: FileManager.default.currentDirectoryPath, isDirectory: true)
        if let root = Self.findRepositoryRoot(from: current) { return root }
        if let executable = Bundle.main.executableURL,
           let root = Self.findRepositoryRoot(from: executable.deletingLastPathComponent()) {
            return root
        }
        throw BridgeClientError.repositoryNotFound
    }

    private static func findRepositoryRoot(from initial: URL) -> URL? {
        var candidate = initial.standardizedFileURL
        for _ in 0 ..< 8 {
            if isRepositoryRoot(candidate) { return candidate }
            candidate.deleteLastPathComponent()
        }
        return nil
    }

    private static func isRepositoryRoot(_ url: URL) -> Bool {
        FileManager.default.fileExists(
            atPath: url.appendingPathComponent("bridge/merlin_bridge.py").path
        )
    }

    private func readResponseLine() async throws -> Data {
        guard let output else { throw BridgeClientError.bridgeTerminated }
        let initialBuffer = unreadOutput
        let read = try await Task.detached(priority: .userInitiated) {
            try Self.readLineSynchronously(from: output, initialBuffer: initialBuffer)
        }.value
        unreadOutput = read.remainder
        return read.line
    }

    private static func readLineSynchronously(
        from handle: FileHandle,
        initialBuffer: Data
    ) throws -> (line: Data, remainder: Data) {
        var buffer = initialBuffer
        let newline = Data([0x0A])
        while true {
            if let range = buffer.range(of: newline) {
                var line = buffer.subdata(in: buffer.startIndex ..< range.lowerBound)
                if line.last == 0x0D { line.removeLast() }
                return (line, buffer.subdata(in: range.upperBound ..< buffer.endIndex))
            }
            if buffer.count > BridgeResponseDecoder.maxResponseLineBytes {
                throw BridgeProtocolError.responseTooLarge
            }
            let chunk = handle.availableData
            guard !chunk.isEmpty else { throw BridgeClientError.bridgeTerminated }
            buffer.append(chunk)
        }
    }
}

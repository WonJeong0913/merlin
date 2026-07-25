import AppKit
import Foundation

enum ExternalTerminalAdapterError: LocalizedError {
    case unsafeSpec
    case terminalUnavailable

    var errorDescription: String? {
        switch self {
        case .unsafeSpec: "The bridge returned an unsafe account-connection command."
        case .terminalUnavailable: "Terminal could not be opened for Codex device authorization."
        }
    }
}

/// Device auth remains outside the app process. The app neither captures nor stores Terminal output.
enum ExternalTerminalAdapter {
    static func launch(_ spec: ConnectSpec) throws {
        guard spec.executable.hasPrefix("/"),
              !spec.executable.contains("\n"),
              spec.arguments == ["login", "--device-auth"] else {
            throw ExternalTerminalAdapterError.unsafeSpec
        }
        let command = "\(shellQuote(spec.executable)) login --device-auth"
        let script = """
        tell application \"Terminal\"
            activate
            do script \"\(appleScriptQuote(command))\"
        end tell
        """
        let launcher = Process()
        launcher.executableURL = URL(fileURLWithPath: "/usr/bin/osascript")
        launcher.arguments = ["-e", script]
        launcher.standardOutput = FileHandle.nullDevice
        launcher.standardError = FileHandle.nullDevice
        do {
            try launcher.run()
        } catch {
            throw ExternalTerminalAdapterError.terminalUnavailable
        }
    }

    private static func shellQuote(_ value: String) -> String {
        "'" + value.replacingOccurrences(of: "'", with: "'\\\"'\\\"'") + "'"
    }

    private static func appleScriptQuote(_ value: String) -> String {
        value
            .replacingOccurrences(of: "\\", with: "\\\\")
            .replacingOccurrences(of: "\"", with: "\\\"")
            .replacingOccurrences(of: "\n", with: "")
    }
}

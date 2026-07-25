import Foundation

/// Commands discoverable in the native composer. Only `.skill` is
/// provider-facing: it remains a normal `chat.send` request for the bridge to
/// interpret. The rest navigate locally or call an existing approval contract.
enum SlashCommand: String, CaseIterable, Identifiable, Equatable {
    case skills
    case harness
    case createSkill = "create-skill"
    case approve
    case reject
    case skill

    var id: String { rawValue }

    var template: String {
        switch self {
        case .createSkill, .skill:
            "/\(rawValue) "
        default:
            "/\(rawValue)"
        }
    }

    var forwardsToProvider: Bool { self == .skill }

    static func parse(_ text: String) -> SlashCommandInvocation? {
        let trimmed = text.trimmingCharacters(in: .whitespacesAndNewlines)
        guard trimmed.first == "/" else { return nil }
        let components = trimmed.split(maxSplits: 1, whereSeparator: { $0.isWhitespace })
        guard let token = components.first,
              let command = SlashCommand(rawValue: String(token.dropFirst()).lowercased()) else {
            return nil
        }
        let arguments = components.count == 2
            ? String(components[1]).trimmingCharacters(in: .whitespacesAndNewlines)
            : ""
        return SlashCommandInvocation(command: command, arguments: arguments)
    }

    static func suggestions(for draft: String) -> [SlashCommand] {
        let trimmed = draft.trimmingCharacters(in: .whitespacesAndNewlines)
        guard trimmed.first == "/" else { return [] }
        let token = trimmed.dropFirst()
        guard !token.contains(where: { $0.isWhitespace }) else { return [] }
        return allCases.filter { $0.rawValue.hasPrefix(token.lowercased()) }
    }
}

struct SlashCommandInvocation: Equatable {
    let command: SlashCommand
    let arguments: String
}

/// Explicitly communicates that no bridge mutation occurred for an unavailable
/// command. It must never be displayed as successful skill creation.
struct SlashCommandProposal: Equatable {
    let command: SlashCommand
    let title: String
    let detail: String
    let suggestedPrompt: String?
}

import Foundation

struct LocalChatTranscriptItem: Codable, Equatable {
    let role: String
    let text: String
}

struct LocalSessionHistoryEntry: Codable, Identifiable, Equatable {
    let id: UUID
    let title: String
    let workspaceName: String
    let workspacePath: String
    let isProject: Bool
    let startedAt: Date
    let transcript: [LocalChatTranscriptItem]?
    /// Stored for local identification only; its presence never means provider-thread resume succeeded.
    let providerThreadID: String?

    init(
        id: UUID,
        title: String,
        workspaceName: String,
        workspacePath: String,
        isProject: Bool,
        startedAt: Date,
        transcript: [LocalChatTranscriptItem]? = nil,
        providerThreadID: String? = nil
    ) {
        self.id = id
        self.title = title
        self.workspaceName = workspaceName
        self.workspacePath = workspacePath
        self.isProject = isProject
        self.startedAt = startedAt
        self.transcript = transcript
        self.providerThreadID = providerThreadID
    }

    private enum CodingKeys: String, CodingKey {
        case id, title, workspaceName, workspacePath, isProject, startedAt, transcript, providerThreadID
    }

    init(from decoder: Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        id = try container.decode(UUID.self, forKey: .id)
        workspaceName = try container.decode(String.self, forKey: .workspaceName)
        workspacePath = try container.decode(String.self, forKey: .workspacePath)
        startedAt = try container.decode(Date.self, forKey: .startedAt)
        title = try container.decodeIfPresent(String.self, forKey: .title) ?? "New chat"
        isProject = try container.decodeIfPresent(Bool.self, forKey: .isProject) ?? false
        transcript = try container.decodeIfPresent([LocalChatTranscriptItem].self, forKey: .transcript)
        providerThreadID = try container.decodeIfPresent(String.self, forKey: .providerThreadID)
    }

    var isRestorable: Bool { !(transcript ?? []).isEmpty }
}

struct LocalSessionHistoryStore {
    static let storageKey = "merlin.localSessionHistory"
    static let maximumCount = 200
    static let placeholderTitles = Set(["New chat", "새 채팅"])

    let defaults: UserDefaults

    init(defaults: UserDefaults = .standard) {
        self.defaults = defaults
    }

    func recentEntries() -> [LocalSessionHistoryEntry] {
        let entries = storedEntries()
        let meaningful = entries.filter { !Self.placeholderTitles.contains($0.title) }
        return Array(meaningful.sorted { $0.startedAt > $1.startedAt }.prefix(Self.maximumCount))
    }

    @discardableResult
    func record(
        workspace: URL,
        title: String = "New chat",
        isProject: Bool = false,
        transcript: [LocalChatTranscriptItem]? = nil,
        providerThreadID: String? = nil,
        date: Date = Date()
    ) -> UUID {
        let canonical = workspace.standardizedFileURL
        let id = UUID()
        let entry = LocalSessionHistoryEntry(
            id: id,
            title: title,
            workspaceName: canonical.lastPathComponent,
            workspacePath: canonical.path,
            isProject: isProject,
            startedAt: date,
            transcript: transcript,
            providerThreadID: providerThreadID
        )
        let all = Array(([entry] + storedEntries())
            .sorted { $0.startedAt > $1.startedAt }
            .prefix(Self.maximumCount))
        save(all)
        return id
    }

    @discardableResult
    func renameEntry(id: UUID, to candidate: String) -> Bool {
        guard let title = WorkspaceStore.validatedDisplayName(candidate) else { return false }
        var entries = storedEntries()
        guard let index = entries.firstIndex(where: { $0.id == id }) else { return false }
        let old = entries[index]
        entries[index] = LocalSessionHistoryEntry(
            id: old.id,
            title: title,
            workspaceName: old.workspaceName,
            workspacePath: old.workspacePath,
            isProject: old.isProject,
            startedAt: old.startedAt,
            transcript: old.transcript,
            providerThreadID: old.providerThreadID
        )
        save(entries)
        return true
    }

    @discardableResult
    func removeEntry(id: UUID) -> Bool {
        let entries = storedEntries()
        let retained = entries.filter { $0.id != id }
        guard retained.count < entries.count else { return false }
        save(retained)
        return true
    }

    /// Reclassifies only the local chat-history metadata; no workspace files move.
    @discardableResult
    func moveEntry(id: UUID, toProject project: URL) -> Bool {
        var entries = storedEntries()
        guard let index = entries.firstIndex(where: { $0.id == id }) else { return false }
        let old = entries[index]
        let canonical = project.standardizedFileURL
        entries[index] = LocalSessionHistoryEntry(
            id: old.id,
            title: old.title,
            workspaceName: canonical.lastPathComponent,
            workspacePath: canonical.path,
            isProject: true,
            startedAt: old.startedAt,
            transcript: old.transcript,
            providerThreadID: old.providerThreadID
        )
        save(entries)
        return true
    }

    @discardableResult
    func updateSnapshot(
        id: UUID,
        transcript: [LocalChatTranscriptItem],
        providerThreadID: String?
    ) -> Bool {
        var entries = storedEntries()
        guard let index = entries.firstIndex(where: { $0.id == id }) else { return false }
        let old = entries[index]
        entries[index] = LocalSessionHistoryEntry(
            id: old.id,
            title: old.title,
            workspaceName: old.workspaceName,
            workspacePath: old.workspacePath,
            isProject: old.isProject,
            startedAt: old.startedAt,
            transcript: transcript,
            providerThreadID: providerThreadID
        )
        save(entries)
        return true
    }

    private func storedEntries() -> [LocalSessionHistoryEntry] {
        guard let data = defaults.data(forKey: Self.storageKey),
              let entries = try? JSONDecoder().decode([LocalSessionHistoryEntry].self, from: data) else {
            return []
        }
        return entries
    }

    private func save(_ entries: [LocalSessionHistoryEntry]) {
        guard let data = try? JSONEncoder().encode(entries) else { return }
        defaults.set(data, forKey: Self.storageKey)
    }
}

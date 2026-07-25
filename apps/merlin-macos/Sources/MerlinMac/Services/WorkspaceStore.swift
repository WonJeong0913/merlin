import Foundation

enum WorkspaceDirectoryError: Error, Equatable {
    case invalidName
    case parentUnavailable
    case alreadyExists
    case createFailed
}

struct WorkspaceStore {
    static let recentPathsKey = "merlin.recentWorkspacePaths"
    static let projectPathsKey = "merlin.projectPaths"
    static let projectAliasesKey = "merlin.projectDisplayAliases"
    static let maximumRecentCount = 3
    static let maximumProjectCount = 20
    static let managedFolderName = "Merlin"
    static let generalWorkspaceName = "General Workspace"

    let defaults: UserDefaults
    let fileManager: FileManager
    let applicationSupportDirectory: URL

    init(
        defaults: UserDefaults = .standard,
        fileManager: FileManager = .default,
        applicationSupportDirectory: URL? = nil
    ) {
        self.defaults = defaults
        self.fileManager = fileManager
        self.applicationSupportDirectory = applicationSupportDirectory
            ?? fileManager.urls(for: .applicationSupportDirectory, in: .userDomainMask)[0]
    }

    func recentDirectories() -> [URL] {
        let paths = defaults.stringArray(forKey: Self.recentPathsKey) ?? []
        var seen = Set<String>()
        let directories = paths.compactMap { path -> URL? in
            let url = URL(fileURLWithPath: path).standardizedFileURL
            var isDirectory: ObjCBool = false
            guard fileManager.fileExists(atPath: url.path, isDirectory: &isDirectory), isDirectory.boolValue else { return nil }
            guard seen.insert(url.path).inserted else { return nil }
            return url
        }
        let limited = Array(directories.prefix(Self.maximumRecentCount))
        if limited.map(\.path) != paths { defaults.set(limited.map(\.path), forKey: Self.recentPathsKey) }
        return limited
    }

    func remember(_ directory: URL) {
        let canonical = directory.standardizedFileURL
        var isDirectory: ObjCBool = false
        guard fileManager.fileExists(atPath: canonical.path, isDirectory: &isDirectory), isDirectory.boolValue else { return }
        let remaining = recentDirectories().map(\.path).filter { $0 != canonical.path }
        defaults.set(Array(([canonical.path] + remaining).prefix(Self.maximumRecentCount)), forKey: Self.recentPathsKey)
    }

    /// The app-owned default chat folder. It never becomes a user Project.
    func managedGeneralWorkspace() throws -> URL {
        let destination = applicationSupportDirectory
            .appendingPathComponent(Self.managedFolderName, isDirectory: true)
            .appendingPathComponent(Self.generalWorkspaceName, isDirectory: true)
            .standardizedFileURL
        var isDirectory: ObjCBool = false
        if fileManager.fileExists(atPath: destination.path, isDirectory: &isDirectory) {
            guard isDirectory.boolValue else { throw WorkspaceDirectoryError.createFailed }
            return destination
        }
        do {
            try fileManager.createDirectory(at: destination, withIntermediateDirectories: true)
            return destination
        } catch {
            throw WorkspaceDirectoryError.createFailed
        }
    }

    func projectDirectories() -> [URL] {
        let paths = defaults.stringArray(forKey: Self.projectPathsKey) ?? []
        var seen = Set<String>()
        let directories = paths.compactMap { path -> URL? in
            let url = URL(fileURLWithPath: path).standardizedFileURL
            var isDirectory: ObjCBool = false
            guard fileManager.fileExists(atPath: url.path, isDirectory: &isDirectory), isDirectory.boolValue else { return nil }
            guard seen.insert(url.path).inserted else { return nil }
            return url
        }
        let limited = Array(directories.prefix(Self.maximumProjectCount))
        if limited.map(\.path) != paths { defaults.set(limited.map(\.path), forKey: Self.projectPathsKey) }
        return limited
    }

    /// Only an explicit Add project action calls this; chat sessions never do.
    func addProject(_ directory: URL) {
        let canonical = directory.standardizedFileURL
        var isDirectory: ObjCBool = false
        guard fileManager.fileExists(atPath: canonical.path, isDirectory: &isDirectory), isDirectory.boolValue,
              canonical.path != (try? managedGeneralWorkspace())?.path else { return }
        let remaining = projectDirectories().map(\.path).filter { $0 != canonical.path }
        defaults.set(Array(([canonical.path] + remaining).prefix(Self.maximumProjectCount)), forKey: Self.projectPathsKey)
    }

    /// App-only label; this never renames the directory on disk.
    func projectDisplayName(for directory: URL) -> String {
        let canonical = directory.standardizedFileURL
        let aliases = defaults.dictionary(forKey: Self.projectAliasesKey) ?? [:]
        return (aliases[canonical.path] as? String) ?? canonical.lastPathComponent
    }

    @discardableResult
    func renameProjectAlias(_ directory: URL, to candidate: String) -> Bool {
        let canonical = directory.standardizedFileURL
        guard projectDirectories().contains(where: { $0.path == canonical.path }),
              let displayName = Self.validatedDisplayName(candidate) else { return false }
        var aliases = defaults.dictionary(forKey: Self.projectAliasesKey) ?? [:]
        aliases[canonical.path] = displayName
        defaults.set(aliases, forKey: Self.projectAliasesKey)
        return true
    }

    /// Removes only the app's saved project entry. It never removes disk content.
    @discardableResult
    func removeProject(_ directory: URL) -> Bool {
        let canonical = directory.standardizedFileURL
        let retained = projectDirectories().map(\.path).filter { $0 != canonical.path }
        guard retained.count < projectDirectories().count else { return false }
        defaults.set(retained, forKey: Self.projectPathsKey)
        var aliases = defaults.dictionary(forKey: Self.projectAliasesKey) ?? [:]
        aliases.removeValue(forKey: canonical.path)
        defaults.set(aliases, forKey: Self.projectAliasesKey)
        return true
    }

    func createDirectory(named name: String, in parent: URL) throws -> URL {
        let validatedName = try Self.validatedFolderName(name)
        let canonicalParent = parent.standardizedFileURL
        var isDirectory: ObjCBool = false
        guard fileManager.fileExists(atPath: canonicalParent.path, isDirectory: &isDirectory), isDirectory.boolValue else {
            throw WorkspaceDirectoryError.parentUnavailable
        }
        let destination = canonicalParent.appendingPathComponent(validatedName, isDirectory: true).standardizedFileURL
        guard !fileManager.fileExists(atPath: destination.path) else { throw WorkspaceDirectoryError.alreadyExists }
        do {
            try fileManager.createDirectory(at: destination, withIntermediateDirectories: false)
        } catch {
            throw WorkspaceDirectoryError.createFailed
        }
        return destination
    }

    static func validatedFolderName(_ candidate: String) throws -> String {
        let name = candidate.trimmingCharacters(in: .whitespacesAndNewlines)
        let forbidden = CharacterSet(charactersIn: "/\\:\0")
        guard !name.isEmpty,
              name != ".", name != "..",
              name.rangeOfCharacter(from: forbidden) == nil else {
            throw WorkspaceDirectoryError.invalidName
        }
        return name
    }

    static func validatedDisplayName(_ candidate: String) -> String? {
        let name = candidate.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !name.isEmpty, name.count <= 80 else { return nil }
        return name
    }
}

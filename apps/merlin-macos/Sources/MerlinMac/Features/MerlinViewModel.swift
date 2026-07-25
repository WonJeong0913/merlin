import AppKit
import Combine
import Foundation
import SwiftUI

struct SafeBridgeError: LocalizedError {
    let message: String
    var errorDescription: String? { message }
}

@MainActor
final class MerlinViewModel: ObservableObject {
    private enum SessionPreferenceKey {
        static let model = "merlin.nextSession.model"
        static let effort = "merlin.nextSession.effort"
        static let routing = "merlin.nextSession.routing"
        static let autonomy = "merlin.nextSession.autonomy"
        static let appearance = "merlin.appearance"
    }

    @Published var state = AppState()
    @Published var draft = ""
    @Published var showDrawer = false
    @Published var showHarnessMap = false
    @Published var harnessMapMode: HarnessMapMode = .graph
    @Published var showLanguagePicker = false
    @Published var showWorkspacePathPicker = false
    /// A sidebar-only account entry point. It must not reset an active provider thread.
    @Published var showAccountConnectionSheet = false
    @Published private(set) var accountConnectionError: String?
    @Published private(set) var isAddingProject = false
    @Published private(set) var recentWorkspaces: [URL] = []
    @Published private(set) var projects: [URL] = []
    @Published private(set) var workspaceIssue: WorkspaceDirectoryError?
    @Published private(set) var recentSessionHistory: [LocalSessionHistoryEntry] = []
    @Published private(set) var isViewingRestoredChat = false
    @Published private(set) var queuedSteeringPrompt: String?
    @Published var projectsExpanded = true
    @Published var chatsExpanded = true
    @Published var selectedHarnessNodeID: String?
    @Published private(set) var governance: HarnessGovernance?
    @Published private(set) var governanceError: String?
    @Published private(set) var selectedCLIExecutable: String?
    @Published private(set) var accountModels: [AccountModelOption] = []
    @Published private(set) var accountModelsAvailable = false
    @Published private(set) var language: AppLanguage
    @Published var preferredColorScheme: ColorScheme

    private let bridge: any BridgeTransport
    private let workspaceStore: WorkspaceStore
    private let sessionHistoryStore: LocalSessionHistoryStore
    private let previewName: String?
    private var pendingChatTitle: String?
    private var currentChatRecorded = false
    private var currentChatHistoryID: UUID?

    init(
        bridge: any BridgeTransport = BridgeClient(),
        workspaceStore: WorkspaceStore = WorkspaceStore(),
        sessionHistoryStore: LocalSessionHistoryStore = LocalSessionHistoryStore()
    ) {
        self.bridge = bridge
        self.workspaceStore = workspaceStore
        self.sessionHistoryStore = sessionHistoryStore
        language = AppLanguage(rawValue: UserDefaults.standard.string(forKey: AppLanguage.storageKey) ?? "") ?? .deviceDefault
        // Light is the primary appearance: the brand mark is a light liquid-glass
        // flower and the palette is sampled from it. Dark remains available and
        // a stored preference still wins.
        preferredColorScheme = UserDefaults.standard.string(forKey: SessionPreferenceKey.appearance) == "dark" ? .dark : .light
        previewName = ProcessInfo.processInfo.environment["MERLIN_UI_PREVIEW"]
        state.nextSettings = Self.loadNextSessionSettings(from: workspaceStore.defaults)
        let isEmptyWorkspacePreview = _isDebugAssertConfiguration()
            && (previewName == "workspace-empty" || previewName == "workspace-empty-popup")
        recentWorkspaces = isEmptyWorkspacePreview ? [] : workspaceStore.recentDirectories()
        projects = workspaceStore.projectDirectories()
        recentSessionHistory = sessionHistoryStore.recentEntries()
    }

    var copy: AppCopy { AppCopy(language: language) }
    var isUIPreview: Bool { _isDebugAssertConfiguration() && previewName != nil }

    var slashCommandSuggestions: [SlashCommand] {
        let matches = SlashCommand.suggestions(for: draft)
        if state.phase == .approvalPending {
            return matches.filter { $0 == .approve || $0 == .reject }
        }
        return matches
    }

    var composerCanEdit: Bool {
        !isViewingRestoredChat && [.chatReady, .turnRunning, .approvalPending].contains(state.phase)
    }

    private var isChatPresentationActive: Bool {
        [.chatReady, .turnRunning, .approvalPending].contains(state.phase)
    }

    var composerCanSubmit: Bool {
        let prompt = draft.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !prompt.isEmpty, composerCanEdit else { return false }
        guard state.phase == .approvalPending else { return true }
        guard let invocation = SlashCommand.parse(prompt) else { return false }
        return invocation.command == .approve || invocation.command == .reject
    }

    func selectSlashCommand(_ command: SlashCommand) {
        draft = command.template
    }

    var currentWorkspaceIsProject: Bool {
        guard let workspace = state.workspace?.standardizedFileURL.path else { return false }
        return projects.contains { $0.standardizedFileURL.path == workspace }
    }

    var headerWorkspaceLabel: String {
        guard currentWorkspaceIsProject, let workspace = state.workspace else {
            return copy.general
        }
        return projectDisplayName(workspace)
    }

    func projectDisplayName(_ project: URL) -> String {
        workspaceStore.projectDisplayName(for: project)
    }

    func cliEvidenceText(copy: AppCopy) -> String {
        guard !isUIPreview else { return "UI Preview" }
        guard let account = state.account, account.connected else {
            return copy.text("Codex CLI unavailable", "Codex CLI 확인 불가")
        }
        let details = [account.cliVersion, account.authMethod].compactMap { $0 }.joined(separator: " · ")
        return details.isEmpty ? copy.codexCLIConnected : "\(copy.codexCLIConnected) · \(details)"
    }

    func providerEvidenceText(copy: AppCopy) -> String {
        guard !isUIPreview else { return "UI Preview" }
        guard state.providerTurnVerified else { return copy.providerNotVerifiedYet }
        let active = state.activeSettings ?? state.nextSettings
        return copy.providerTurnCompleted(model: copy.modelLabel(active.model))
    }

    var nextModelValidationMessage: String? {
        do {
            _ = try state.nextSettings.requestedModelID()
            return nil
        } catch {
            return copy.text(
                "Use 1-128 letters, digits, periods, underscores, or hyphens. Leave blank for the Codex default.",
                "영문·숫자·마침표·밑줄·하이픈 1~128자만 사용할 수 있습니다. 비워 두면 Codex 기본값을 사용합니다."
            )
        }
    }

    var nextSupportedEfforts: [String] {
        guard let selectedModel = selectedNextAccountModel,
              !selectedModel.supportedEfforts.isEmpty else {
            return SessionSettings.fallbackEfforts
        }
        return selectedModel.supportedEfforts
    }

    var selectedNextAccountModel: AccountModelOption? {
        state.nextSettings.model.isEmpty
            ? accountModels.first(where: \.isDefault)
            : accountModels.first(where: { $0.id == state.nextSettings.model })
    }

    func selectNextModel(_ modelID: String) {
        state.nextSettings.model = modelID
        guard let selected = selectedNextAccountModel,
            !selected.supportedEfforts.isEmpty else { return }
        if let defaultEffort = selected.defaultEffort {
            state.nextSettings.effort = defaultEffort
        } else if !selected.supportedEfforts.contains(state.nextSettings.effort) {
            state.nextSettings.effort = selected.supportedEfforts[0]
        }
    }

    @discardableResult
    func saveNextSessionSettings() -> Bool {
        guard nextModelValidationMessage == nil else { return false }
        let settings = state.nextSettings
        let defaults = workspaceStore.defaults
        defaults.set(settings.model, forKey: SessionPreferenceKey.model)
        defaults.set(settings.effort, forKey: SessionPreferenceKey.effort)
        defaults.set(settings.routingMode, forKey: SessionPreferenceKey.routing)
        defaults.set(settings.autonomyMode, forKey: SessionPreferenceKey.autonomy)
        showDrawer = false
        return true
    }

    /// Persist the controls and apply them from the next provider turn while
    /// preserving the active Codex thread, transcript, trace root, and map.
    func saveAndApplyNextSessionSettings() async {
        guard saveNextSessionSettings() else { return }
        guard state.account?.connected == true,
              state.phase == .chatReady,
              state.activeSettings != state.nextSettings else { return }
        do {
            var payload: [String: BridgeValue] = [
                "effort": .string(state.nextSettings.effort),
                "routing_mode": .string(state.nextSettings.routingMode),
                "autonomy_mode": .string(state.nextSettings.autonomyMode)
            ]
            if let model = try state.nextSettings.requestedModelID() {
                payload["model"] = .string(model)
            }
            let response = try await bridge.request(command: .sessionUpdateSettings, payload: payload)
            guard response.ok, response.event == "session.settings_updated" else {
                throw SafeBridgeError(message: response.error ?? "The session settings were not updated.")
            }
            state.activeSettings = state.nextSettings
            await refreshSessionStatus()
        } catch {
            state.failSafely(error)
        }
    }

    private static func loadNextSessionSettings(from defaults: UserDefaults) -> SessionSettings {
        let model = defaults.string(forKey: SessionPreferenceKey.model) ?? ""
        let safeModel = model.isEmpty || SessionSettings.isSafeModelID(model) ? model : ""
        let effort = defaults.string(forKey: SessionPreferenceKey.effort) ?? "xhigh"
        let routing = defaults.string(forKey: SessionPreferenceKey.routing) ?? "semantic"
        let autonomy = defaults.string(forKey: SessionPreferenceKey.autonomy) ?? "managed"
        var settings = SessionSettings()
        settings.model = safeModel
        settings.effort = SessionSettings.fallbackEfforts.contains(effort) ? effort : "xhigh"
        settings.routingMode = ["semantic", "deterministic"].contains(routing) ? routing : "semantic"
        settings.autonomyMode = ["managed", "strict"].contains(autonomy) ? autonomy : "managed"
        return settings
    }

    var connectionNoticeText: String? {
        guard state.connectionNotice != nil else { return nil }
        return copy.text(
            "Terminal opened for device authorization. Complete it there, then select Refresh status. The app does not read or retain the device code or Terminal output.",
            "기기 인증을 위해 터미널을 열었습니다. 그곳에서 인증한 뒤 상태 새로고침을 선택하세요. 앱은 기기 코드나 터미널 출력을 읽거나 보관하지 않습니다."
        )
    }

    func setLanguage(_ language: AppLanguage) {
        self.language = language
        UserDefaults.standard.set(language.rawValue, forKey: AppLanguage.storageKey)
    }

    func toggleAppearance() {
        preferredColorScheme = preferredColorScheme == .dark ? .light : .dark
        UserDefaults.standard.set(preferredColorScheme == .light ? "light" : "dark", forKey: SessionPreferenceKey.appearance)
    }

    func bootstrap() async {
        // Bounded visual fixture: active only in an assert-enabled (non-release) build.
        if _isDebugAssertConfiguration(),
           let preview = ProcessInfo.processInfo.environment["MERLIN_UI_PREVIEW"] {
            switch preview {
            case "logged_out":
                state.phase = .loggedOut
                return
            case "connected":
                // Preview state intentionally carries no CLI or provider evidence.
                state.account = nil
                state.phase = .connected
                return
            case "workspace-empty", "workspace-empty-popup":
                state.workspace = nil
                recentWorkspaces = []
                workspaceIssue = nil
                showWorkspacePathPicker = preview == "workspace-empty-popup"
                state.phase = .workspaceSelection
                return
            case "chat-empty":
                activateDebugChatPreview(drawer: false, harness: false)
                return
            case "chat-drawer":
                activateDebugChatPreview(drawer: true, harness: false)
                return
            case "harness-empty":
                activateDebugChatPreview(drawer: false, harness: true)
                return
            case "chat-selection":
                activateDebugChatPreview(drawer: true, harness: false)
                seedDebugProvisionedSkills()
                return
            default:
                break
            }
        }
        guard state.phase == .checking || state.phase == .safeError else { return }
        state.phase = .checking
        do {
            let hello = try await bridge.request(command: .hello, payload: [:])
            guard hello.ok, hello.event == "bridge.hello" else {
                throw SafeBridgeError(message: hello.error ?? "The local bridge did not identify itself.")
            }
            try await refreshAccountStatus()
        } catch {
            state.failSafely(error)
        }
    }

    func refreshAccountStatus(preservingCurrentSession: Bool = false) async throws {
        let preservesPresentation = preservingCurrentSession && isChatPresentationActive
        if !preservesPresentation {
            state.phase = .checking
        }
        let payload = selectedCLIExecutable.map { ["executable": BridgeValue.string($0)] } ?? [:]
        let response = try await bridge.request(command: .accountStatus, payload: payload)
        let account = try AccountStatus(data: try responseData(response))
        if preservesPresentation {
            // Account recovery is auxiliary to the visible chat. Updating its
            // evidence must never restart, clear, or route away from the thread.
            state.account = account
        } else {
            state.applyAccount(account)
        }
        if account.connected && !preservesPresentation {
            // Authentication is the launch gate. Model catalog discovery is an
            // optional picker enhancement and must never hold the account UI.
            Task { await refreshAccountModels() }
        } else if !account.connected {
            accountModels = []
            accountModelsAvailable = false
        }
    }

    /// Failure is deliberately non-fatal: the drawer retains Codex default + safe custom ID fallback.
    func refreshAccountModels() async {
        guard !isUIPreview, state.account?.connected == true else {
            accountModels = []
            accountModelsAvailable = false
            return
        }
        do {
            let payload = selectedCLIExecutable.map { ["executable": BridgeValue.string($0)] } ?? [:]
            let response = try await bridge.request(command: .accountModels, payload: payload)
            let catalog = try AccountModelCatalog(data: try responseData(response))
            accountModelsAvailable = catalog.available
            accountModels = catalog.models
            selectNextModel(state.nextSettings.model)
        } catch {
            accountModels = []
            accountModelsAvailable = false
        }
    }

    func openAccountConnectionSheet() {
        accountConnectionError = nil
        showAccountConnectionSheet = true
    }

    func refreshAccountStatusPreservingSession() async {
        do {
            try await refreshAccountStatus(preservingCurrentSession: true)
            accountConnectionError = nil
            if state.account?.connected == true { state.connectionNotice = nil }
        } catch {
            // Keep bridge details out of the UI and leave the active chat untouched.
            accountConnectionError = copy.text(
                "Could not refresh Codex connection status. Check the CLI and try again.",
                "Codex 연결 상태를 새로고침하지 못했습니다. CLI를 확인한 뒤 다시 시도하세요."
            )
        }
    }

    func connectAccount(preservingCurrentSession: Bool = false) async {
        let preservesPresentation = preservingCurrentSession && isChatPresentationActive
        do {
            let payload = selectedCLIExecutable.map { ["executable": BridgeValue.string($0)] } ?? [:]
            let response = try await bridge.request(command: .accountConnectSpec, payload: payload)
            let spec = try ConnectSpec(data: try responseData(response))
            try ExternalTerminalAdapter.launch(spec)
            state.connectionNotice = "device_authorization_opened"
            accountConnectionError = nil
            if !preservesPresentation {
                state.phase = .loggedOut
            }
        } catch {
            if preservesPresentation {
                accountConnectionError = copy.text(
                    "Could not open device authorization. Check the Codex CLI and try again.",
                    "기기 인증을 열지 못했습니다. Codex CLI를 확인한 뒤 다시 시도하세요."
                )
            } else {
                state.failSafely(error)
            }
        }
    }

    @discardableResult
    func chooseCodexCLIExecutable(preservingCurrentSession: Bool = false) -> Bool {
        let panel = NSOpenPanel()
        panel.canChooseFiles = true
        panel.canChooseDirectories = false
        panel.allowsMultipleSelection = false
        panel.prompt = copy.text("Use Codex CLI", "Codex CLI 사용")
        panel.message = copy.text("Choose the Codex CLI executable. The path is used only for this app session and is not persisted.", "Codex CLI 실행 파일을 선택하세요. 이 경로는 현재 앱 세션에서만 사용되며 저장되지 않습니다.")
        guard panel.runModal() == .OK, let url = panel.url else { return false }
        selectedCLIExecutable = url.path
        let preservesPresentation = preservingCurrentSession && isChatPresentationActive
        Task { await refreshAfterConnection(preservingCurrentSession: preservesPresentation) }
        return true
    }

    func refreshAfterConnection(preservingCurrentSession: Bool = false) async {
        if preservingCurrentSession {
            await refreshAccountStatusPreservingSession()
            return
        }
        do {
            try await refreshAccountStatus()
            if state.phase == .connected { state.connectionNotice = nil }
        } catch {
            state.failSafely(error)
        }
    }

    func startGeneralChat() async {
        guard state.account?.connected == true, !isUIPreview else { return }
        do {
            let generalWorkspace = try workspaceStore.managedGeneralWorkspace()
            let requiresFreshSession = state.activeSettings == nil
                || state.workspace?.standardizedFileURL.path != generalWorkspace.standardizedFileURL.path
                || state.activeSettings != state.nextSettings
            state.selectWorkspace(generalWorkspace)
            workspaceIssue = nil
            resetChatTransientSurface()
            if requiresFreshSession || state.phase != .chatReady {
                await startSession()
            } else {
                await startNewThread()
            }
        } catch let issue as WorkspaceDirectoryError {
            workspaceIssue = issue
            state.failSafely(issue)
        } catch {
            state.failSafely(error)
        }
    }

    func beginAddProject() {
        guard state.account?.connected == true, !isUIPreview else { return }
        workspaceIssue = nil
        isAddingProject = true
        showWorkspacePathPicker = true
    }

    @discardableResult
    func chooseWorkspace() -> Bool {
        let panel = NSOpenPanel()
        panel.canChooseFiles = false
        panel.canChooseDirectories = true
        panel.allowsMultipleSelection = false
        panel.canCreateDirectories = true
        panel.prompt = copy.text("Choose existing folder", "기존 폴더 선택")
        panel.message = copy.text("The agent will use this local folder for the selected session.", "에이전트가 선택한 세션에서 이 로컬 폴더를 사용합니다.")
        guard panel.runModal() == .OK, let url = panel.url else { return false }
        selectWorkspace(url)
        return true
    }

    func selectRecentWorkspace(_ url: URL) {
        Task { await openProject(url) }
    }

    @discardableResult
    func createNewWorkspace() -> Bool {
        let parentPanel = NSOpenPanel()
        parentPanel.canChooseFiles = false
        parentPanel.canChooseDirectories = true
        parentPanel.allowsMultipleSelection = false
        parentPanel.canCreateDirectories = false
        parentPanel.prompt = copy.text("Choose parent", "상위 폴더 선택")
        parentPanel.message = copy.text("Choose the parent directory for the new workspace folder.", "새 작업 공간 폴더를 만들 상위 디렉터리를 선택하세요.")
        guard parentPanel.runModal() == .OK, let parent = parentPanel.url else { return false }

        let alert = NSAlert()
        alert.messageText = copy.text("Create new folder", "새 폴더 만들기")
        alert.informativeText = copy.text("Parent: \(parent.path)\nEnter a new folder name. An existing folder will never be overwritten.", "상위 폴더: \(parent.path)\n새 폴더 이름을 입력하세요. 기존 폴더는 절대 덮어쓰지 않습니다.")
        let nameField = NSTextField(string: "")
        nameField.placeholderString = copy.text("Folder name", "폴더 이름")
        nameField.frame = NSRect(x: 0, y: 0, width: 300, height: 24)
        alert.accessoryView = nameField
        alert.addButton(withTitle: copy.text("Create", "생성"))
        alert.addButton(withTitle: copy.text("Cancel", "취소"))
        guard alert.runModal() == .alertFirstButtonReturn else { return false }

        do {
            let created = try workspaceStore.createDirectory(named: nameField.stringValue, in: parent)
            selectWorkspace(created)
            return true
        } catch let issue as WorkspaceDirectoryError {
            workspaceIssue = issue
        } catch {
            workspaceIssue = .createFailed
        }
        return false
    }

    func startSession() async {
        guard let workspace = state.workspace else {
            state.failSafely(SafeBridgeError(message: "Choose a workspace folder before starting a session."))
            return
        }
        do {
            let command: BridgeCommand = state.activeSettings == nil ? .sessionStart : .sessionRestart
            var payload: [String: BridgeValue] = [
                "workspace": .string(workspace.path),
                "effort": .string(state.nextSettings.effort),
                "routing_mode": .string(state.nextSettings.routingMode),
                "autonomy_mode": .string(state.nextSettings.autonomyMode)
            ]
            if let model = try state.nextSettings.requestedModelID() {
                payload["model"] = .string(model)
            }
            if let selectedCLIExecutable {
                payload["executable"] = .string(selectedCLIExecutable)
            }
            state.beginSession()
            let response = try await bridge.request(
                command: command,
                payload: payload
            )
            let expectedEvent = command == .sessionStart ? "session.started" : "session.restarted"
            guard response.ok, response.event == expectedEvent else {
                throw SafeBridgeError(message: response.error ?? "The session did not start.")
            }
            state.sessionDidStart()
            resetChatTransientSurface()
            pendingChatTitle = nil
            currentChatRecorded = false
            currentChatHistoryID = nil
            isViewingRestoredChat = false
            reloadSessionHistory()
            await refreshSessionStatus()
        } catch {
            state.failSafely(error)
        }
    }

    func sendDraft() async {
        let prompt = draft.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !prompt.isEmpty else { return }
        if recordedEvidenceIntent(for: prompt) == "recorded-controlled-shadowing-recovery-v1" {
            draft = ""
            prepareChatTitle(from: prompt)
            state.presentRecordedEvidence(
                prompt: prompt,
                response: copy.text(
                    """
                    Verified recorded result — Shadowing Recovery

                    • Problem: the overloaded 10-task controlled fixture passed 1/10, with measured skill shadowing at 89%.
                    • Diagnosis: the trace linked failures to two distractor skills that shadowed the correct routes.
                    • Intervention: both distractors were hidden through copy-on-write governance; the source library stayed unchanged.
                    • Verification: the same verifier suite reran and recovered to 9/10, shadowing fell to 0%, and all 5/5 lifecycle gates passed.
                    • Decision: the governed change was promoted.

                    Evidence boundary: this is retained SHA-256-bound controlled evidence—not a new provider turn, not provider-native skill invocation, and not a general benchmark claim. Open Harness Map → Evidence to inspect its source path and hash.
                    """,
                    """
                    검증된 기록 결과 — Shadowing Recovery

                    • 문제: 과부하된 10-task 통제 fixture는 1/10만 통과했고, 측정된 skill shadowing은 89%였습니다.
                    • 진단: trace가 올바른 route를 가린 두 distractor skill과 실패를 연결했습니다.
                    • 개입: 두 distractor를 copy-on-write governance로 hide했으며 source library는 변경하지 않았습니다.
                    • 검증: 동일 verifier suite를 다시 실행해 9/10으로 회복했고 shadowing은 0%로 감소했으며 lifecycle gate 5/5를 통과했습니다.
                    • 결정: 관리된 변경을 promotion했습니다.

                    증거 경계: 이는 SHA-256으로 결합된 보존 통제 증거이며 새 provider turn, provider-native skill invocation, 일반 benchmark 주장이 아닙니다. Harness Map → Evidence에서 source path와 hash를 확인할 수 있습니다.
                    """
                )
            )
            recordCurrentChatIfNeeded()
            harnessMapMode = .evidence
            return
        }
        if let invocation = SlashCommand.parse(prompt), !invocation.command.forwardsToProvider {
            draft = ""
            await executeSlashCommand(invocation)
            return
        }
        if state.phase == .turnRunning {
            queuedSteeringPrompt = prompt
            draft = ""
            return
        }
        guard state.phase == .chatReady, !isViewingRestoredChat else { return }
        draft = ""
        prepareChatTitle(from: prompt)
        state.beginTurn(prompt: prompt)
        await send(prompt: prompt)
    }

    private func recordedEvidenceIntent(for prompt: String) -> String? {
        let normalized = prompt.folding(
            options: [.caseInsensitive, .diacriticInsensitive],
            locale: Locale(identifier: "en_US_POSIX")
        )
        let asksForShadowing = normalized.contains("shadowing") || normalized.contains("shadow")
        let asksForRecordedResult = ["verified", "evidence", "result", "recovery", "before", "after"]
            .contains { normalized.contains($0) }
        guard asksForShadowing, asksForRecordedResult else { return nil }
        return state.sessionStatus?.recordedEvidence.contains { evidence in
            evidence.id == "recorded-controlled-shadowing-recovery-v1"
        } == true ? "recorded-controlled-shadowing-recovery-v1" : nil
    }

    private func executeSlashCommand(_ invocation: SlashCommandInvocation) async {
        switch invocation.command {
        case .skills:
            guard invocation.arguments.isEmpty else {
                appendCommandProposal(
                    command: .skills,
                    title: copy.text("/skills takes no arguments", "/skills는 인수를 받지 않습니다"),
                    detail: copy.text("Use /skills to browse bridge-returned skill contracts.", "/skills를 사용하면 브리지가 반환한 스킬 계약을 살펴볼 수 있습니다."),
                    suggestedPrompt: nil
                )
                return
            }
            harnessMapMode = .skills
            showHarnessMap = true
        case .harness:
            guard invocation.arguments.isEmpty else {
                appendCommandProposal(
                    command: .harness,
                    title: copy.text("/harness takes no arguments", "/harness는 인수를 받지 않습니다"),
                    detail: copy.text("Use /harness to inspect declared contracts and returned runtime evidence.", "/harness를 사용하면 선언된 계약과 반환된 런타임 증거를 확인할 수 있습니다."),
                    suggestedPrompt: nil
                )
                return
            }
            harnessMapMode = .graph
            showHarnessMap = true
        case .createSkill:
            let request = invocation.arguments.isEmpty
                ? copy.text("Describe the capability, inputs, outputs, and validation gates.", "필요한 기능, 입력, 출력, 검증 게이트를 설명하세요.")
                : invocation.arguments
            appendCommandProposal(
                command: .createSkill,
                title: copy.text("Direct skill creation is unavailable", "직접 스킬 생성은 사용할 수 없습니다"),
                detail: copy.text(
                    "This bridge has no skill.create command. Nothing was created or changed. You can draft a governed request; the active session may return an approval card if creation is supported for that request.",
                    "이 브리지에는 skill.create 명령이 없습니다. 생성되거나 변경된 항목은 없습니다. 관리형 요청 초안을 만들 수 있으며, 현재 세션이 해당 요청의 생성을 지원하면 승인 카드를 반환할 수 있습니다."
                ),
                suggestedPrompt: copy.text(
                    "Create a governed skill request. \(request)",
                    "관리형 스킬 생성 요청을 준비해 줘. \(request)"
                )
            )
        case .approve, .reject:
            guard invocation.arguments.isEmpty else {
                appendCommandProposal(
                    command: invocation.command,
                    title: copy.text("Approval commands take no arguments", "승인 명령은 인수를 받지 않습니다"),
                    detail: copy.text("Use /approve or /reject only for the approval card currently shown in this thread.", "이 스레드에 표시된 승인 카드에만 /approve 또는 /reject를 사용하세요."),
                    suggestedPrompt: nil
                )
                return
            }
            guard state.phase == .approvalPending, state.pendingApproval != nil else {
                appendCommandProposal(
                    command: invocation.command,
                    title: copy.text("No approval is waiting", "대기 중인 승인이 없습니다"),
                    detail: copy.text("This command did not call the bridge or change any capability.", "이 명령은 브리지를 호출하거나 어떤 기능도 변경하지 않았습니다."),
                    suggestedPrompt: nil
                )
                return
            }
            let approved = invocation.command == .approve
            await resolveApproval(
                approved: approved,
                commandCompletionNote: approved
                    ? copy.text("Approval resolved through /approve.", "/approve로 승인을 처리했습니다.")
                    : copy.text("Approval resolved through /reject.", "/reject로 승인을 거절했습니다.")
            )
        case .skill:
            // Provider-facing slash commands intentionally remain in chat.send.
            break
        }
    }

    private func appendCommandProposal(
        command: SlashCommand,
        title: String,
        detail: String,
        suggestedPrompt: String?
    ) {
        let proposal = SlashCommandProposal(
            command: command,
            title: title,
            detail: detail,
            suggestedPrompt: suggestedPrompt
        )
        state.messages.append(ChatMessage(
            kind: .commandProposal,
            text: detail,
            commandProposal: proposal
        ))
    }

    func sendSuggestion(_ prompt: String) async {
        guard state.phase == .chatReady, !isViewingRestoredChat else { return }
        prepareChatTitle(from: prompt)
        state.beginTurn(prompt: prompt)
        await send(prompt: prompt)
    }

    func resolveApproval(approved: Bool, commandCompletionNote: String? = nil) async {
        guard state.phase == .approvalPending else { return }
        state.phase = .turnRunning
        do {
            let response = try await bridge.request(
                command: .approvalResolve,
                payload: ["approved": .bool(approved)]
            )
            try applyChatResponse(response)
            if let commandCompletionNote {
                state.messages.append(ChatMessage(kind: .note, text: commandCompletionNote))
            }
            await runQueuedSteeringIfReady()
        } catch {
            state.failSafely(error)
        }
    }

    func recordFeedback(_ outcome: String) async {
        guard state.phase == .chatReady, !state.feedbackRecorded else { return }
        do {
            let response = try await bridge.request(
                command: .feedbackRecord,
                payload: ["outcome": .string(outcome)]
            )
            guard response.ok, response.event == "feedback.recorded" else {
                throw SafeBridgeError(message: response.error ?? "Feedback was not recorded.")
            }
            state.recordFeedback(try FeedbackRecord(data: try responseData(response)))
            await refreshSessionStatus()
        } catch {
            state.failSafely(error)
        }
    }

    func startNewThread() async {
        guard state.phase == .chatReady else { return }
        state.phase = .turnRunning
        do {
            let response = try await bridge.request(command: .sessionNewThread, payload: [:])
            guard response.ok, response.event == "session.new_thread" else {
                throw SafeBridgeError(message: response.error ?? "A new provider thread could not start.")
            }
            state.startNewProviderThread()
            pendingChatTitle = nil
            currentChatRecorded = false
            currentChatHistoryID = nil
            isViewingRestoredChat = false
            resetChatTransientSurface()
            await refreshSessionStatus()
        } catch {
            state.failSafely(error)
        }
    }

    func startNewChat() async {
        guard state.account?.connected == true, state.phase == .chatReady else { return }
        // New chat means a new provider thread in the current workspace. It
        // must not silently move a project conversation back to General.
        if state.activeSettings != state.nextSettings {
            await saveAndApplyNextSessionSettings()
            guard state.phase == .chatReady,
                  state.activeSettings == state.nextSettings else { return }
        }
        await startNewThread()
    }

    func openProject(_ project: URL) async {
        guard state.account?.connected == true, !isUIPreview,
              projects.contains(where: { $0.standardizedFileURL.path == project.standardizedFileURL.path }),
              state.phase == .connected || state.phase == .chatReady else { return }
        state.selectWorkspace(project)
        workspaceIssue = nil
        resetChatTransientSurface()
        await startSession()
    }

    @discardableResult
    func renameProject(_ project: URL, to title: String) -> Bool {
        guard workspaceStore.renameProjectAlias(project, to: title) else { return false }
        reloadProjects()
        return true
    }

    @discardableResult
    func removeProject(_ project: URL) -> Bool {
        guard workspaceStore.removeProject(project) else { return false }
        reloadProjects()
        return true
    }

    @discardableResult
    func renameChat(_ entry: LocalSessionHistoryEntry, to title: String) -> Bool {
        guard sessionHistoryStore.renameEntry(id: entry.id, to: title) else { return false }
        reloadSessionHistory()
        return true
    }

    @discardableResult
    func removeChat(_ entry: LocalSessionHistoryEntry) -> Bool {
        guard sessionHistoryStore.removeEntry(id: entry.id) else { return false }
        reloadSessionHistory()
        return true
    }

    @discardableResult
    func moveChat(_ entry: LocalSessionHistoryEntry, toProject project: URL) -> Bool {
        guard projects.contains(where: { $0.standardizedFileURL.path == project.standardizedFileURL.path }),
              sessionHistoryStore.moveEntry(id: entry.id, toProject: project) else { return false }
        reloadSessionHistory()
        return true
    }

    /// Restore the local transcript and prepare its retained Codex thread.
    /// Provider resume is proven only when the next turn completes.
    func restoreChat(_ entry: LocalSessionHistoryEntry) async {
        guard entry.isRestorable, state.phase == .chatReady else { return }
        let workspace = URL(fileURLWithPath: entry.workspacePath, isDirectory: true).standardizedFileURL
        var isDirectory: ObjCBool = false
        guard FileManager.default.fileExists(atPath: workspace.path, isDirectory: &isDirectory),
              isDirectory.boolValue else {
            state.failSafely(SafeBridgeError(message: copy.text("The saved workspace is no longer available.", "저장된 작업 공간을 찾을 수 없습니다.")))
            return
        }
        let restoredMessages = (entry.transcript ?? []).map { item in
            let kind: ChatMessageKind
            switch item.role {
            case "user": kind = .user
            case "assistant": kind = .assistant
            default: kind = .note
            }
            return ChatMessage(kind: kind, text: item.text)
        }
        state.selectWorkspace(workspace)
        workspaceIssue = nil
        isViewingRestoredChat = true
        await startSession()
        guard state.phase == .chatReady else { return }
        do {
            if let threadID = entry.providerThreadID, !threadID.isEmpty {
                state.phase = .turnRunning
                let response = try await bridge.request(
                    command: .sessionResumeThread,
                    payload: ["thread_id": .string(threadID)]
                )
                guard response.ok, response.event == "session.resume_thread" else {
                    throw SafeBridgeError(message: response.error ?? "The saved provider thread could not be prepared.")
                }
            }
            state.messages = restoredMessages
            state.phase = .chatReady
            state.providerTurnVerified = false
            pendingChatTitle = entry.title
            currentChatRecorded = true
            currentChatHistoryID = entry.id
            isViewingRestoredChat = false
            resetChatTransientSurface()
            await refreshSessionStatus()
        } catch {
            isViewingRestoredChat = false
            state.failSafely(error)
        }
    }

    func requestRenameProject(_ project: URL) {
        requestRename(
            title: copy.text("Rename project", "프로젝트 이름 변경"),
            prompt: copy.text("This changes only the app display name. The folder name is unchanged.", "앱에 표시되는 이름만 바뀝니다. 실제 폴더 이름은 변경되지 않습니다."),
            current: projectDisplayName(project)
        ) { [weak self] in _ = self?.renameProject(project, to: $0) }
    }

    func requestRemoveProject(_ project: URL) {
        guard confirm(
            title: copy.text("Remove project?", "프로젝트를 목록에서 제거할까요?"),
            message: copy.text("This removes only the project from Merlin. The folder and all files stay on disk.", "멀린의 프로젝트 목록에서만 제거합니다. 실제 폴더와 파일은 디스크에 그대로 유지됩니다."),
            confirmTitle: copy.text("Remove", "제거")
        ) else { return }
        _ = removeProject(project)
    }

    func requestRenameChat(_ entry: LocalSessionHistoryEntry) {
        requestRename(
            title: copy.text("Rename chat", "채팅 이름 변경"),
            prompt: copy.text("This changes only the local chat-history title.", "로컬 채팅 이력의 제목만 변경합니다."),
            current: entry.title
        ) { [weak self] in _ = self?.renameChat(entry, to: $0) }
    }

    func requestRemoveChat(_ entry: LocalSessionHistoryEntry) {
        guard confirm(
            title: copy.text("Delete chat history?", "채팅 이력을 삭제할까요?"),
            message: copy.text("This deletes only the local history entry. It does not delete any workspace files or provider data.", "로컬 이력 항목만 삭제합니다. 작업공간 파일이나 공급자 데이터는 삭제하지 않습니다."),
            confirmTitle: copy.text("Delete", "삭제")
        ) else { return }
        _ = removeChat(entry)
    }

    func refreshSessionStatus() async {
        guard [.chatReady, .approvalPending].contains(state.phase) else { return }
        do {
            let response = try await bridge.request(command: .sessionStatus, payload: [:])
            state.sessionStatus = try SessionStatus(data: try responseData(response))
        } catch {
            state.safeErrorMessage = error.localizedDescription
        }
    }

    /// Governance state is disk-backed and session-independent, so unlike
    /// `refreshSessionStatus()` this does not require an active session and its
    /// failure never escalates into the app-wide safe-error surface.
    func refreshGovernance() async {
        governanceError = nil
        do {
            let response = try await bridge.request(command: .harnessGovernance, payload: [:])
            governance = try HarnessGovernance(data: try responseData(response))
        } catch {
            governance = nil
            governanceError = error.localizedDescription
        }
    }

    func dismissError() {
        if state.account?.connected == true {
            state.phase = .connected
        } else {
            state.phase = .loggedOut
        }
        state.safeErrorMessage = nil
    }

    func shutdown() async {
        await bridge.shutdown()
    }

    private func send(prompt: String) async {
        do {
            let response = try await bridge.request(command: .chatSend, payload: ["text": .string(prompt)])
            try applyChatResponse(response)
            await runQueuedSteeringIfReady()
        } catch {
            state.failSafely(error)
        }
    }

    private func runQueuedSteeringIfReady() async {
        guard state.phase == .chatReady, let prompt = queuedSteeringPrompt else { return }
        queuedSteeringPrompt = nil
        prepareChatTitle(from: prompt)
        state.beginTurn(prompt: prompt)
        await send(prompt: prompt)
    }

    private func applyChatResponse(_ response: BridgeResponse) throws {
        guard response.ok else { throw SafeBridgeError(message: response.error ?? "The local bridge stopped safely.") }
        switch response.event {
        case "chat.completed":
            state.completeTurn(try TurnDetails(data: try responseData(response)))
            recordCurrentChatIfNeeded()
            Task { await self.refreshSessionStatus() }
        case "approval.required":
            state.requireApproval(try ApprovalRequest(data: try responseData(response)))
        case "approval.declined":
            state.approvalDeclined()
        default:
            throw BridgeProtocolError.unsupportedEvent(response.event)
        }
    }

    private func responseData(_ response: BridgeResponse) throws -> [String: BridgeValue] {
        guard response.ok else { throw SafeBridgeError(message: response.error ?? "The local bridge stopped safely.") }
        guard let data = response.data else { throw BridgeProtocolError.missingData }
        return data
    }

    private func selectWorkspace(_ url: URL) {
        state.selectWorkspace(url)
        workspaceStore.remember(url)
        reloadRecentWorkspaces()
        workspaceIssue = nil
        if isAddingProject {
            isAddingProject = false
            workspaceStore.addProject(url)
            reloadProjects()
            resetChatTransientSurface()
            Task { await self.openProject(url) }
        }
    }

    private func reloadRecentWorkspaces() {
        recentWorkspaces = workspaceStore.recentDirectories()
    }

    private func reloadProjects() {
        projects = workspaceStore.projectDirectories()
    }

    private func reloadSessionHistory() {
        recentSessionHistory = sessionHistoryStore.recentEntries()
    }

    private func requestRename(title: String, prompt: String, current: String, apply: (String) -> Void) {
        let alert = NSAlert()
        alert.messageText = title
        alert.informativeText = prompt
        let field = NSTextField(string: current)
        field.frame = NSRect(x: 0, y: 0, width: 300, height: 24)
        alert.accessoryView = field
        alert.addButton(withTitle: copy.text("Save", "저장"))
        alert.addButton(withTitle: copy.text("Cancel", "취소"))
        guard alert.runModal() == .alertFirstButtonReturn else { return }
        apply(field.stringValue)
    }

    private func confirm(title: String, message: String, confirmTitle: String) -> Bool {
        let alert = NSAlert()
        alert.messageText = title
        alert.informativeText = message
        alert.addButton(withTitle: confirmTitle)
        alert.addButton(withTitle: copy.text("Cancel", "취소"))
        return alert.runModal() == .alertFirstButtonReturn
    }

    private func prepareChatTitle(from prompt: String) {
        guard pendingChatTitle == nil else { return }
        let normalized = prompt.split(whereSeparator: { $0.isWhitespace }).joined(separator: " ")
        guard !normalized.isEmpty else { return }
        let limit = 44
        pendingChatTitle = normalized.count > limit
            ? String(normalized.prefix(limit)).trimmingCharacters(in: .whitespaces) + "…"
            : normalized
    }

    private func recordCurrentChatIfNeeded() {
        guard let workspace = state.workspace,
              let title = pendingChatTitle else { return }
        let transcript = state.messages.compactMap { message -> LocalChatTranscriptItem? in
            let role: String
            switch message.kind {
            case .user: role = "user"
            case .assistant: role = "assistant"
            case .note: role = "note"
            case .turnDetails, .approval, .commandProposal: return nil
            }
            return LocalChatTranscriptItem(role: role, text: message.text)
        }
        guard !transcript.isEmpty else { return }
        if let historyID = currentChatHistoryID {
            _ = sessionHistoryStore.updateSnapshot(
                id: historyID,
                transcript: transcript,
                providerThreadID: state.lastTurn?.threadID
            )
        } else {
            currentChatHistoryID = sessionHistoryStore.record(
                workspace: workspace,
                title: title,
                isProject: currentWorkspaceIsProject,
                transcript: transcript,
                providerThreadID: state.lastTurn?.threadID
            )
        }
        currentChatRecorded = true
        reloadSessionHistory()
    }

    func resetChatTransientSurface() {
        showHarnessMap = false
        harnessMapMode = .graph
        showDrawer = false
        selectedHarnessNodeID = nil
        queuedSteeringPrompt = nil
    }

    /// Fill the session-drawer selection diagnostics without spending a
    /// provider turn.
    ///
    /// The fixture is decoded through the real `TurnDetails(data:)` initializer
    /// rather than assembled directly, so a drifted bridge contract breaks the
    /// preview instead of quietly rendering something production cannot produce.
    /// Skill IDs are prefixed `preview/` so nothing here can be mistaken for a
    /// recorded decision.
    private func seedDebugProvisionedSkills() {
        func skill(_ id: String, _ name: String, _ score: Double, _ why: String) -> BridgeValue {
            .object([
                "skill_id": .string("preview/\(id)"),
                "name": .string(name),
                "score": .number(score),
                "why": .string(why),
            ])
        }
        let payload: [String: BridgeValue] = [
            "answer": .string("Preview turn. No provider request was made."),
            "thread_id": .string("preview-thread"),
            "turn_number": .number(1),
            "routing_decision": .object([
                "routing_source": .string("deterministic"),
                "candidate_skill_count": .number(209),
                "authoritative_final_decision": .bool(true),
                "final_provisioned_ids": .array([
                    .string("preview/pdf"),
                    .string("preview/academic-pdf-redaction"),
                    .string("preview/marker"),
                ]),
            ]),
            "provisioned_skills": .array([
                skill("pdf", "PDF", 0.333, "Trigger and description both match the requested form-filling artifact."),
                skill("academic-pdf-redaction", "Academic PDF Redaction", 0.210, "Shares the PDF artifact anchor but targets redaction, not form completion."),
                skill("marker", "Marker", 0.180, "Document-conversion overlap only; ranked last inside the exposure budget."),
            ]),
            "evidence": .object([
                "raw_trace_pointer": .string("preview.jsonl"),
                "prompt_provisioning_is_native_invocation": .bool(false),
            ]),
        ]
        state.lastTurn = try? TurnDetails(data: payload)
    }

    private func activateDebugChatPreview(drawer: Bool, harness: Bool) {
        // Debug-only visual fixtures; no bridge request or production session record is created.
        state.account = nil
        state.workspace = URL(fileURLWithPath: "/tmp/merlin-preview-workspace", isDirectory: true)
        state.activeSettings = SessionSettings()
        state.messages = []
        state.lastTurn = nil
        state.pendingApproval = nil
        state.sessionStatus = nil
        state.harnessMap = HarnessMapState()
        state.providerTurnVerified = false
        state.phase = .chatReady
        showDrawer = drawer
        showHarnessMap = harness
        selectedHarnessNodeID = nil
    }
}

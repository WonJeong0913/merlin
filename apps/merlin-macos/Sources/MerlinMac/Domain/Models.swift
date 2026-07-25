import Foundation

enum AppPhase: String, Equatable {
    case checking
    case loggedOut
    case connected
    case workspaceSelection
    case sessionStarting
    case chatReady
    case turnRunning
    case approvalPending
    case safeError
}

struct AccountStatus: Equatable {
    let state: String
    let connected: Bool
    let executable: String?
    let cliVersion: String?
    let authMethod: String?

    init(data: [String: BridgeValue]) throws {
        guard let state = data["state"]?.string,
              let connected = data["connected"]?.bool else {
            throw BridgeProtocolError.invalidData("account status")
        }
        self.state = state
        self.connected = connected
        executable = data["executable"]?.string
        cliVersion = data["cli_version"]?.string
        authMethod = data["auth_method"]?.string
    }
}

struct ConnectSpec: Equatable {
    let executable: String
    let arguments: [String]
    let cliVersion: String?

    init(data: [String: BridgeValue]) throws {
        guard data["transport"]?.string == "pty",
              let executable = data["executable"]?.string,
              let arguments = data["arguments"]?.array?.compactMap(\.string),
              arguments == ["login", "--device-auth"] else {
            throw BridgeProtocolError.invalidData("account connection specification")
        }
        self.executable = executable
        self.arguments = arguments
        cliVersion = data["cli_version"]?.string
    }
}

/// A non-sensitive model choice returned by the authenticated local Codex app-server.
/// This is intentionally account/runtime data rather than an app-side model allowlist.
struct AccountModelOption: Identifiable, Equatable {
    let id: String
    let displayName: String
    let description: String
    let isDefault: Bool
    let defaultEffort: String?
    let supportedEfforts: [String]

    init(data: [String: BridgeValue]) throws {
        guard let id = data["id"]?.string,
              SessionSettings.isSafeModelID(id) else {
            throw BridgeProtocolError.invalidData("account model")
        }
        self.id = id
        displayName = data["display_name"]?.string ?? id
        description = data["description"]?.string ?? ""
        isDefault = data["is_default"]?.bool ?? false
        let efforts = data["supported_efforts"]?.array?.compactMap(\.string) ?? []
        var normalizedEfforts: [String] = []
        for effort in efforts where !normalizedEfforts.contains(effort) {
            normalizedEfforts.append(effort)
        }
        supportedEfforts = normalizedEfforts
        let candidateDefault = data["default_effort"]?.string
        defaultEffort = candidateDefault.flatMap { normalizedEfforts.contains($0) ? $0 : nil }
    }
}

struct AccountModelCatalog: Equatable {
    let available: Bool
    let models: [AccountModelOption]

    init(data: [String: BridgeValue]) throws {
        guard let available = data["available"]?.bool else {
            throw BridgeProtocolError.invalidData("account model catalog")
        }
        self.available = available
        models = data["models"]?.array?.compactMap { value in
            guard let object = value.object else { return nil }
            return try? AccountModelOption(data: object)
        } ?? []
    }
}

struct SessionSettings: Equatable {
    enum ModelIDError: LocalizedError, Equatable {
        case invalid

        var errorDescription: String? {
            "Model ID must use 1-128 letters, digits, periods, underscores, or hyphens."
        }
    }

    /// Empty means omit `--model` and use the account's Codex default model.
    var model = ""
    var effort = "xhigh"
    var routingMode = "semantic"
    var autonomyMode = "managed"

    static let fallbackEfforts = ["low", "medium", "high", "xhigh", "max", "ultra"]

    static func isSafeModelID(_ value: String) -> Bool {
        let pattern = "^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$"
        return value.utf8.count <= 128
            && value.range(of: pattern, options: .regularExpression) != nil
    }

    func requestedModelID() throws -> String? {
        guard !model.isEmpty else { return nil }
        guard Self.isSafeModelID(model) else {
            throw ModelIDError.invalid
        }
        return model
    }
}

struct ProvisionedSkill: Identifiable, Equatable {
    let skillID: String
    let name: String
    let score: Double
    let why: String

    var id: String { skillID }

    init(data: [String: BridgeValue]) throws {
        guard let skillID = data["skill_id"]?.string,
              let name = data["name"]?.string,
              let score = data["score"].flatMap({ value -> Double? in
                  if case let .number(number) = value { return number }
                  return nil
              }),
              let why = data["why"]?.string else {
            throw BridgeProtocolError.invalidData("provisioned skill")
        }
        self.skillID = skillID
        self.name = name
        self.score = score
        self.why = why
    }
}

struct HarnessAction: Identifiable, Equatable {
    let id: UUID
    let status: String
    let scope: String?
    let gateCount: Int?
    let sourceLibraryUnchanged: Bool?
    let action: String?
    let riskClass: String?
    let permissionRequired: Bool?
    let plannedMutations: [String]

    init(data: [String: BridgeValue]) throws {
        guard let status = data["status"]?.string else {
            throw BridgeProtocolError.invalidData("harness action")
        }
        id = UUID()
        self.status = status
        scope = data["scope"]?.string
        gateCount = data["gate_count"]?.int
        sourceLibraryUnchanged = data["source_library_unchanged"]?.bool
        let proposal = data["proposal"]?.object
        action = proposal?["action"]?.string
        riskClass = proposal?["risk_class"]?.string
        permissionRequired = proposal?["permission_required"]?.bool
        plannedMutations = proposal?["planned_mutations"]?.array?.compactMap(\.string) ?? []
    }
}

struct RoutingObservation: Equatable {
    let source: String
    let finalProvisionedIDs: [String]
    let abstainReason: String?
    let candidateCount: Int
    let authoritative: Bool

    init(data: [String: BridgeValue]) throws {
        guard let source = data["routing_source"]?.string,
              let candidateCount = data["candidate_skill_count"]?.int,
              let authoritative = data["authoritative_final_decision"]?.bool else {
            throw BridgeProtocolError.invalidData("routing observation")
        }
        self.source = source
        self.candidateCount = candidateCount
        self.authoritative = authoritative
        finalProvisionedIDs = data["final_provisioned_ids"]?.array?.compactMap(\.string) ?? []
        abstainReason = data["final_abstain_reason"]?.string
    }
}

enum HarnessEvidenceKind: String, Equatable {
    case promptExposure = "prompt exposure"
    case selectionObservation = "selection observation"
    case feedbackOutcome = "feedback outcome"
    case copyOnWriteAction = "copy-on-write action"
    case lifecycleEvent = "lifecycle event"
}

enum HarnessNodeKind: String, Equatable {
    case turn
    case skill
    case action
}

enum HarnessEdgeKind: String, Equatable {
    case provisionedFor = "provisioned for"
    case actionFor = "action for"
    case feedbackFor = "feedback for"
}

struct HarnessEvent: Identifiable, Equatable {
    let id: UUID
    let kind: HarnessEvidenceKind
    let title: String
    let detail: String
    let tracePointer: String?

    init(kind: HarnessEvidenceKind, title: String, detail: String, tracePointer: String? = nil) {
        id = UUID()
        self.kind = kind
        self.title = title
        self.detail = detail
        self.tracePointer = tracePointer
    }
}

struct HarnessNode: Identifiable, Equatable {
    let id: String
    let kind: HarnessNodeKind
    let label: String
    let evidenceKind: HarnessEvidenceKind
    let tracePointer: String?
}

struct HarnessEdge: Identifiable, Equatable {
    let id: String
    let sourceID: String
    let targetID: String
    let kind: HarnessEdgeKind
    let evidenceKind: HarnessEvidenceKind
}

struct FeedbackRecord: Equatable {
    let turnNumber: Int
    let outcome: String
    let tracePointer: String?

    init(data: [String: BridgeValue]) throws {
        guard let turnNumber = data["turn_number"]?.int,
              let outcome = data["outcome"]?.string else {
            throw BridgeProtocolError.invalidData("feedback record")
        }
        self.turnNumber = turnNumber
        self.outcome = outcome
        tracePointer = data["raw_trace"]?.object?["pointer"]?.string
    }
}

/// The map stores only evidence the desktop bridge actually returned. It is
/// deliberately ready for future lifecycle events without fabricating them.
struct HarnessMapState: Equatable {
    private(set) var events: [HarnessEvent] = []
    private(set) var nodes: [HarnessNode] = []
    private(set) var edges: [HarnessEdge] = []

    var hasLiveLifecycleEvents: Bool {
        events.contains { $0.kind == .lifecycleEvent }
    }

    mutating func record(turn: TurnDetails) {
        let turnID = "turn:\(turn.threadID):\(turn.turnNumber)"
        upsert(HarnessNode(
            id: turnID,
            kind: .turn,
            label: "Turn \(turn.turnNumber)",
            evidenceKind: .selectionObservation,
            tracePointer: turn.rawTracePointer
        ))

        let decision = turn.routingObservation
        let selected = decision.finalProvisionedIDs.isEmpty
            ? "abstained: \(decision.abstainReason ?? "no skill selected")"
            : "selected: \(decision.finalProvisionedIDs.joined(separator: ", "))"
        events.append(HarnessEvent(
            kind: .selectionObservation,
            title: "\(decision.source) routing",
            detail: "\(selected) · \(decision.candidateCount) candidates · authoritative=\(decision.authoritative)",
            tracePointer: turn.rawTracePointer
        ))

        for skill in turn.provisionedSkills {
            let skillID = "skill:\(skill.skillID)"
            upsert(HarnessNode(
                id: skillID,
                kind: .skill,
                label: skill.name,
                evidenceKind: .promptExposure,
                tracePointer: turn.rawTracePointer
            ))
            append(HarnessEdge(
                id: "\(turnID)->\(skillID)",
                sourceID: turnID,
                targetID: skillID,
                kind: .provisionedFor,
                evidenceKind: .promptExposure
            ))
            events.append(HarnessEvent(
                kind: .promptExposure,
                title: "Provisioned \(skill.name)",
                detail: skill.why,
                tracePointer: turn.rawTracePointer
            ))
        }

        for (index, action) in turn.harnessActions.enumerated() {
            let actionID = "action:\(turn.threadID):\(turn.turnNumber):\(index)"
            upsert(HarnessNode(
                id: actionID,
                kind: .action,
                label: action.status,
                evidenceKind: .copyOnWriteAction,
                tracePointer: turn.rawTracePointer
            ))
            append(HarnessEdge(
                id: "\(turnID)->\(actionID)",
                sourceID: turnID,
                targetID: actionID,
                kind: .actionFor,
                evidenceKind: .copyOnWriteAction
            ))
            let gates = action.gateCount.map { " · \($0) gates" } ?? ""
            events.append(HarnessEvent(
                kind: .copyOnWriteAction,
                title: action.status.capitalized,
                detail: (action.scope ?? "bounded harness action") + gates,
                tracePointer: turn.rawTracePointer
            ))
        }
    }

    mutating func record(feedback: FeedbackRecord, threadID: String?) {
        let turnID = threadID.map { "turn:\($0):\(feedback.turnNumber)" } ?? "turn:unknown:\(feedback.turnNumber)"
        events.append(HarnessEvent(
            kind: .feedbackOutcome,
            title: "Feedback: \(feedback.outcome)",
            detail: "Observed health feedback for turn \(feedback.turnNumber); automatic lifecycle change remains deferred.",
            tracePointer: feedback.tracePointer
        ))
        let feedbackID = "feedback:\(turnID):\(feedback.outcome)"
        upsert(HarnessNode(
            id: feedbackID,
            kind: .action,
            label: "Feedback \(feedback.outcome)",
            evidenceKind: .feedbackOutcome,
            tracePointer: feedback.tracePointer
        ))
        append(HarnessEdge(
            id: "\(turnID)->\(feedbackID)",
            sourceID: turnID,
            targetID: feedbackID,
            kind: .feedbackFor,
            evidenceKind: .feedbackOutcome
        ))
    }

    private mutating func upsert(_ node: HarnessNode) {
        if let index = nodes.firstIndex(where: { $0.id == node.id }) {
            nodes[index] = node
        } else {
            nodes.append(node)
        }
    }

    private mutating func append(_ edge: HarnessEdge) {
        guard !edges.contains(where: { $0.id == edge.id }) else { return }
        edges.append(edge)
    }
}

struct TurnDetails: Equatable {
    let answer: String
    let threadID: String
    let turnID: String?
    let turnNumber: Int
    let provisionedSkills: [ProvisionedSkill]
    let routingObservation: RoutingObservation
    let harnessActions: [HarnessAction]
    let rawTracePointer: String
    let promptProvisioningIsNativeInvocation: Bool

    init(data: [String: BridgeValue]) throws {
        guard let answer = data["answer"]?.string,
              let threadID = data["thread_id"]?.string,
              let turnNumber = data["turn_number"]?.int,
              let routing = data["routing_decision"]?.object,
              let evidence = data["evidence"]?.object,
              let rawTracePointer = evidence["raw_trace_pointer"]?.string else {
            throw BridgeProtocolError.invalidData("completed chat turn")
        }
        self.answer = answer
        self.threadID = threadID
        turnID = data["turn_id"]?.string
        self.turnNumber = turnNumber
        routingObservation = try RoutingObservation(data: routing)
        provisionedSkills = try (data["provisioned_skills"]?.array ?? []).map {
            guard let object = $0.object else { throw BridgeProtocolError.invalidData("provisioned skills") }
            return try ProvisionedSkill(data: object)
        }
        harnessActions = try (data["harness_actions"]?.array ?? []).map {
            guard let object = $0.object else { throw BridgeProtocolError.invalidData("harness actions") }
            return try HarnessAction(data: object)
        }
        self.rawTracePointer = rawTracePointer
        promptProvisioningIsNativeInvocation = evidence["prompt_provisioning_is_native_invocation"]?.bool ?? false
    }
}

struct ApprovalRequest: Equatable {
    let message: String
    let capabilityID: String
    let action: String?
    let riskClass: String?
    let plannedMutations: [String]

    init(data: [String: BridgeValue]) throws {
        guard let message = data["message"]?.string,
              let proposal = data["proposal"]?.object,
              let capabilityID = proposal["capability_id"]?.string else {
            throw BridgeProtocolError.invalidData("approval request")
        }
        self.message = message
        self.capabilityID = capabilityID
        action = proposal["action"]?.string
        riskClass = proposal["risk_class"]?.string
        plannedMutations = proposal["planned_mutations"]?.array?.compactMap(\.string) ?? []
    }
}

struct SessionStatus: Equatable {
    let completedTurns: Int
    let activeSkillCount: Int
    let routingMode: String
    let automaticLifecycleChanges: String
    let feedbackPending: Int
    let skillContracts: [SkillContract]
    let hookContracts: [HookContract]
    let recordedEvidence: [RecordedEvidence]
    let declaredRuntimeContract: Bool

    init(data: [String: BridgeValue]) throws {
        guard let completedTurns = data["completed_turns"]?.int,
              let activeSkillCount = data["active_skill_count"]?.int,
              let routingMode = data["routing_mode"]?.string,
              let automaticLifecycleChanges = data["automatic_lifecycle_changes"]?.string,
              let feedback = data["feedback"]?.object,
              let feedbackPending = feedback["pending"]?.int else {
            throw BridgeProtocolError.invalidData("session status")
        }
        self.completedTurns = completedTurns
        self.activeSkillCount = activeSkillCount
        self.routingMode = routingMode
        self.automaticLifecycleChanges = automaticLifecycleChanges
        self.feedbackPending = feedbackPending
        skillContracts = data["skill_contracts"]?.array?.compactMap { value in
            guard let object = value.object else { return nil }
            return try? SkillContract(data: object)
        } ?? []
        hookContracts = data["hook_contracts"]?.array?.compactMap { value in
            guard let object = value.object else { return nil }
            return try? HookContract(data: object)
        } ?? []
        recordedEvidence = data["recorded_evidence"]?.array?.compactMap { value in
            guard let object = value.object else { return nil }
            return try? RecordedEvidence(data: object)
        } ?? []
        declaredRuntimeContract = data["declared_runtime_contract"]?.bool ?? false
    }
}

struct RecordedEvidence: Identifiable, Equatable {
    let id: String
    let title: String
    let kind: String
    let status: String
    let role: String
    let lifecycle: [String]
    let gatesPassed: Int
    let gatesTotal: Int
    let requestedModel: String?
    let modelEvidenceLevel: String
    let actualProviderRun: Bool
    let providerNativeInvocation: Bool
    let sourcePath: String
    let sourceSHA256: String

    init(data: [String: BridgeValue]) throws {
        guard let id = data["id"]?.string,
              let title = data["title"]?.string,
              let kind = data["kind"]?.string,
              let status = data["status"]?.string,
              let role = data["role"]?.string,
              let gatesPassed = data["gates_passed"]?.int,
              let gatesTotal = data["gates_total"]?.int,
              let modelEvidenceLevel = data["model_evidence_level"]?.string,
              let actualProviderRun = data["actual_provider_run"]?.bool,
              let providerNativeInvocation = data["provider_native_invocation"]?.bool,
              let sourcePath = data["source_path"]?.string,
              let sourceSHA256 = data["source_sha256"]?.string,
              sourceSHA256.count == 64 else {
            throw BridgeProtocolError.invalidData("recorded evidence")
        }
        self.id = id
        self.title = title
        self.kind = kind
        self.status = status
        self.role = role
        lifecycle = data["lifecycle"]?.array?.compactMap(\.string) ?? []
        self.gatesPassed = gatesPassed
        self.gatesTotal = gatesTotal
        requestedModel = data["requested_model"]?.string
        self.modelEvidenceLevel = modelEvidenceLevel
        self.actualProviderRun = actualProviderRun
        self.providerNativeInvocation = providerNativeInvocation
        self.sourcePath = sourcePath
        self.sourceSHA256 = sourceSHA256
    }
}

struct SkillContract: Identifiable, Equatable {
    let id: String
    let name: String
    let status: String
    let description: String
    let trigger: String
    let version: Int
    let validators: [String]
    let stepCount: Int
    let edgeCount: Int
    let expectedArtifacts: [String]
    let failureModes: [String]

    init(data: [String: BridgeValue]) throws {
        guard let id = data["id"]?.string,
              let name = data["name"]?.string,
              let status = data["status"]?.string,
              let description = data["description"]?.string,
              let trigger = data["trigger"]?.string,
              let version = data["version"]?.int,
              let stepCount = data["step_count"]?.int,
              let edgeCount = data["edge_count"]?.int else {
            throw BridgeProtocolError.invalidData("skill contract")
        }
        self.id = id
        self.name = name
        self.status = status
        self.description = description
        self.trigger = trigger
        self.version = version
        self.stepCount = stepCount
        self.edgeCount = edgeCount
        validators = data["validators"]?.array?.compactMap(\.string) ?? []
        expectedArtifacts = data["expected_artifacts"]?.array?.compactMap(\.string) ?? []
        failureModes = data["failure_modes"]?.array?.compactMap(\.string) ?? []
    }
}

struct HookContract: Identifiable, Equatable {
    let hook: String
    let permittedMutations: [String]
    let declaredRuntimeContract: Bool

    var id: String { hook }

    init(data: [String: BridgeValue]) throws {
        guard let hook = data["hook"]?.string,
              let declaredRuntimeContract = data["declared_runtime_contract"]?.bool else {
            throw BridgeProtocolError.invalidData("hook contract")
        }
        self.hook = hook
        permittedMutations = data["permitted_mutations"]?.array?.compactMap(\.string) ?? []
        self.declaredRuntimeContract = declaredRuntimeContract
    }
}

enum ChatMessageKind: Equatable {
    case user
    case assistant
    case turnDetails
    case approval
    case commandProposal
    case note
}

struct ChatMessage: Identifiable, Equatable {
    let id: UUID
    let kind: ChatMessageKind
    let text: String
    let turn: TurnDetails?
    let approval: ApprovalRequest?
    let commandProposal: SlashCommandProposal?

    init(
        kind: ChatMessageKind,
        text: String,
        turn: TurnDetails? = nil,
        approval: ApprovalRequest? = nil,
        commandProposal: SlashCommandProposal? = nil
    ) {
        id = UUID()
        self.kind = kind
        self.text = text
        self.turn = turn
        self.approval = approval
        self.commandProposal = commandProposal
    }
}

struct AppState: Equatable {
    var phase: AppPhase = .checking
    var account: AccountStatus?
    var workspace: URL?
    /// Edited in the drawer and sent only with the next `session.start` call.
    var nextSettings = SessionSettings()
    /// Frozen from the successful `session.start` response; never mutated live.
    var activeSettings: SessionSettings?
    var messages: [ChatMessage] = []
    var lastTurn: TurnDetails?
    var pendingApproval: ApprovalRequest?
    var sessionStatus: SessionStatus?
    var harnessMap = HarnessMapState()
    var safeErrorMessage: String?
    var connectionNotice: String?
    var feedbackRecorded = false
    /// True only after a bridge-returned `chat.completed` payload is applied.
    var providerTurnVerified = false

    mutating func applyAccount(_ account: AccountStatus) {
        self.account = account
        safeErrorMessage = nil
        switch account.state {
        case "connected" where account.connected:
            phase = .connected
        case "logged_out", "cli_missing", "check_failed":
            phase = .loggedOut
        default:
            phase = .safeError
            safeErrorMessage = "Codex CLI 상태를 확인하지 못했습니다 (\(account.state))."
        }
    }

    mutating func selectWorkspace(_ url: URL) {
        workspace = url
        safeErrorMessage = nil
    }

    mutating func startWorkspaceSelection() {
        guard account?.connected == true else {
            phase = .safeError
            safeErrorMessage = "Connect a Codex account before selecting a workspace."
            return
        }
        phase = .workspaceSelection
    }

    mutating func beginSession() { phase = .sessionStarting }

    mutating func sessionDidStart() {
        phase = .chatReady
        activeSettings = nextSettings
        messages = []
        lastTurn = nil
        pendingApproval = nil
        sessionStatus = nil
        harnessMap = HarnessMapState()
        feedbackRecorded = false
        providerTurnVerified = false
    }

    mutating func startNewProviderThread() {
        phase = .chatReady
        messages = []
        lastTurn = nil
        pendingApproval = nil
        sessionStatus = nil
        harnessMap = HarnessMapState()
        feedbackRecorded = false
        providerTurnVerified = false
    }

    mutating func beginTurn(prompt: String) {
        messages.append(ChatMessage(kind: .user, text: prompt))
        pendingApproval = nil
        phase = .turnRunning
    }

    mutating func presentRecordedEvidence(prompt: String, response: String) {
        messages.append(ChatMessage(kind: .user, text: prompt))
        messages.append(ChatMessage(kind: .assistant, text: response))
        pendingApproval = nil
        feedbackRecorded = false
        providerTurnVerified = false
        phase = .chatReady
    }

    mutating func completeTurn(_ turn: TurnDetails) {
        lastTurn = turn
        pendingApproval = nil
        feedbackRecorded = false
        harnessMap.record(turn: turn)
        messages.append(ChatMessage(kind: .assistant, text: turn.answer))
        messages.append(ChatMessage(kind: .turnDetails, text: "Turn \(turn.turnNumber)", turn: turn))
        phase = .chatReady
        providerTurnVerified = true
    }

    mutating func requireApproval(_ approval: ApprovalRequest) {
        pendingApproval = approval
        messages.append(ChatMessage(kind: .approval, text: approval.message, approval: approval))
        phase = .approvalPending
    }

    mutating func approvalDeclined() {
        pendingApproval = nil
        messages.append(ChatMessage(kind: .note, text: "Approval denied. The original request was not executed."))
        phase = .chatReady
    }

    mutating func recordFeedback(_ feedback: FeedbackRecord) {
        feedbackRecorded = true
        harnessMap.record(feedback: feedback, threadID: lastTurn?.threadID)
        messages.append(ChatMessage(kind: .note, text: "Feedback recorded as \(feedback.outcome). It is health evidence only; it does not trigger an automatic lifecycle change."))
    }

    mutating func failSafely(_ error: Error) {
        pendingApproval = nil
        phase = .safeError
        safeErrorMessage = error.localizedDescription
    }
}

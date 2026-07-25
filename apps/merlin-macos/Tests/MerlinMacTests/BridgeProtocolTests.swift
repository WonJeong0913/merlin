import Foundation
import XCTest
@testable import MerlinMac

final class BridgeProtocolTests: XCTestCase {
    func testDecodesMatchingAccountStatusResponse() throws {
        let line = Data("""
        {"schema_version":1,"request_id":"req-1","ok":true,"event":"account.status","data":{"state":"connected","connected":true,"executable":"/tmp/codex","cli_version":"codex-cli test","auth_method":"chatgpt"}}
        """.utf8)

        let response = try BridgeResponseDecoder.decode(line, expectedRequestID: "req-1")
        let account = try AccountStatus(data: try data(response))

        XCTAssertTrue(account.connected)
        XCTAssertEqual(account.state, "connected")
        XCTAssertEqual(account.authMethod, "chatgpt")
    }

    func testRejectsRequestIDMismatch() throws {
        let line = Data("{\"schema_version\":1,\"request_id\":\"other\",\"ok\":true,\"event\":\"bridge.hello\"}".utf8)

        XCTAssertThrowsError(try BridgeResponseDecoder.decode(line, expectedRequestID: "expected")) { error in
            XCTAssertEqual(error as? BridgeProtocolError, .requestIDMismatch(expected: "expected", actual: "other"))
        }
    }

    func testRejectsMalformedAndOversizeResponseLines() {
        XCTAssertThrowsError(try BridgeResponseDecoder.decode(Data("not-json".utf8), expectedRequestID: "req")) { error in
            XCTAssertEqual(error as? BridgeProtocolError, .malformedResponse)
        }

        let oversize = Data(repeating: 0x61, count: BridgeResponseDecoder.maxResponseLineBytes + 1)
        XCTAssertThrowsError(try BridgeResponseDecoder.decode(oversize, expectedRequestID: "req")) { error in
            XCTAssertEqual(error as? BridgeProtocolError, .responseTooLarge)
        }
    }

    func testApprovalEventMovesStateWithoutExecutingOriginalRequest() throws {
        var state = AppState()
        state.applyAccount(try AccountStatus(data: [
            "state": .string("connected"),
            "connected": .bool(true)
        ]))
        state.startWorkspaceSelection()
        state.selectWorkspace(URL(fileURLWithPath: "/tmp/project"))
        state.beginSession()
        state.sessionDidStart()
        state.beginTurn(prompt: "Extract TODO entries from backlog.todo and write todo-items.json.")
        let approval = try ApprovalRequest(data: [
            "message": .string("Approval is required."),
            "proposal": .object([
                "capability_id": .string("extract-todo-items"),
                "action": .string("compile_verify_and_stage_registered_skill"),
                "risk_class": .string("low_reversible_registered_operation"),
                "planned_mutations": .array([.string("copy-on-write session overlay")])
            ])
        ])

        state.requireApproval(approval)

        XCTAssertEqual(state.phase, .approvalPending)
        XCTAssertEqual(state.pendingApproval?.capabilityID, "extract-todo-items")
        XCTAssertEqual(state.messages.last?.kind, .approval)
        state.approvalDeclined()
        XCTAssertEqual(state.phase, .chatReady)
        XCTAssertNil(state.pendingApproval)
    }

    func testStateTransitionsIntoARealChatShellOnlyAfterSessionStart() throws {
        var state = AppState()
        state.applyAccount(try AccountStatus(data: ["state": .string("connected"), "connected": .bool(true)]))
        XCTAssertEqual(state.phase, .connected)

        state.startWorkspaceSelection()
        XCTAssertEqual(state.phase, .workspaceSelection)
        state.selectWorkspace(URL(fileURLWithPath: "/tmp/project"))
        state.beginSession()
        XCTAssertEqual(state.phase, .sessionStarting)
        state.sessionDidStart()
        XCTAssertEqual(state.phase, .chatReady)
        XCTAssertTrue(state.messages.isEmpty)
    }

    func testLatestTurnRetainsProvisionedSkillDiagnosticsWithoutInvocationClaim() throws {
        let turn = try TurnDetails(data: [
            "answer": .string("Done."),
            "thread_id": .string("thread-1"),
            "turn_number": .number(1),
            "provisioned_skills": .array([
                .object([
                    "skill_id": .string("report-writer"),
                    "name": .string("Report writer"),
                    "score": .number(0.875),
                    "why": .string("The requested artifact matches its declared output contract.")
                ])
            ]),
            "routing_decision": .object([
                "routing_source": .string("deterministic"),
                "candidate_skill_count": .number(3),
                "final_provisioned_ids": .array([.string("report-writer")]),
                "authoritative_final_decision": .bool(true)
            ]),
            "harness_actions": .array([]),
            "evidence": .object([
                "raw_trace_pointer": .string("turn-0001.jsonl"),
                "prompt_provisioning_is_native_invocation": .bool(false)
            ])
        ])

        var state = AppState()
        state.sessionDidStart()
        state.completeTurn(turn)

        let skill = try XCTUnwrap(state.lastTurn?.provisionedSkills.first)
        XCTAssertEqual(skill.skillID, "report-writer")
        XCTAssertEqual(skill.name, "Report writer")
        XCTAssertEqual(skill.score, 0.875, accuracy: 0.000_001)
        XCTAssertEqual(skill.why, "The requested artifact matches its declared output contract.")
        XCTAssertFalse(try XCTUnwrap(state.lastTurn).promptProvisioningIsNativeInvocation)
    }

    func testHarnessMapAccumulatesOnlyReturnedTurnAndFeedbackEvidence() throws {
        var state = AppState()
        state.sessionDidStart()
        let turn = try TurnDetails(data: [
            "answer": .string("Done."),
            "thread_id": .string("thread-1"),
            "turn_id": .string("turn-1"),
            "turn_number": .number(1),
            "provisioned_skills": .array([
                .object([
                    "skill_id": .string("line-summary"),
                    "name": .string("Line summary"),
                    "score": .number(0.9),
                    "why": .string("Matched the declared input anchor.")
                ])
            ]),
            "routing_decision": .object([
                "routing_source": .string("semantic"),
                "candidate_skill_count": .number(2),
                "final_provisioned_ids": .array([.string("line-summary")]),
                "authoritative_final_decision": .bool(true)
            ]),
            "harness_actions": .array([
                .object([
                    "status": .string("adopted"),
                    "scope": .string("copy_on_write_session_overlay"),
                    "gate_count": .number(7),
                    "source_library_unchanged": .bool(true),
                    "proposal": .object([
                        "action": .string("create_skill"),
                        "risk_class": .string("low"),
                        "permission_required": .bool(false),
                        "planned_mutations": .array([.string("session overlay")])
                    ])
                ])
            ]),
            "evidence": .object([
                "raw_trace_pointer": .string("turn-0001.jsonl"),
                "prompt_provisioning_is_native_invocation": .bool(false)
            ])
        ])

        state.completeTurn(turn)
        state.recordFeedback(try FeedbackRecord(data: [
            "turn_number": .number(1),
            "outcome": .string("pass"),
            "raw_trace": .object(["pointer": .string("turn-0001.jsonl")])
        ]))

        XCTAssertFalse(state.harnessMap.hasLiveLifecycleEvents)
        XCTAssertEqual(state.harnessMap.nodes.filter { $0.kind == .skill }.map(\.label), ["Line summary"])
        XCTAssertEqual(state.harnessMap.edges.map(\.kind), [.provisionedFor, .actionFor, .feedbackFor])
        XCTAssertEqual(state.harnessMap.events.map(\.kind), [.selectionObservation, .promptExposure, .copyOnWriteAction, .feedbackOutcome])

        state.startNewProviderThread()

        XCTAssertEqual(state.harnessMap, HarnessMapState())
        XCTAssertNil(state.sessionStatus)
        XCTAssertFalse(state.providerTurnVerified)
    }

    func testWorkspaceRecentsFilterMissingAndDuplicateDirectories() throws {
        let suite = "MerlinMacTests.workspace-recents-\(UUID().uuidString)"
        let defaults = try XCTUnwrap(UserDefaults(suiteName: suite))
        defer { defaults.removePersistentDomain(forName: suite) }

        let root = FileManager.default.temporaryDirectory.appendingPathComponent("merlin-workspace-recents-\(UUID().uuidString)", isDirectory: true)
        defer { try? FileManager.default.removeItem(at: root) }
        let first = root.appendingPathComponent("first", isDirectory: true)
        let second = root.appendingPathComponent("second", isDirectory: true)
        try FileManager.default.createDirectory(at: first, withIntermediateDirectories: true)
        try FileManager.default.createDirectory(at: second, withIntermediateDirectories: true)

        defaults.set([first.path, "/missing/workspace", first.path, second.path], forKey: WorkspaceStore.recentPathsKey)
        let store = WorkspaceStore(defaults: defaults)

        XCTAssertEqual(store.recentDirectories().map(\.path), [first.standardizedFileURL.path, second.standardizedFileURL.path])
        XCTAssertEqual(defaults.stringArray(forKey: WorkspaceStore.recentPathsKey), [first.standardizedFileURL.path, second.standardizedFileURL.path])
    }

    func testWorkspaceCreationRejectsUnsafeNamesAndCollision() throws {
        let root = FileManager.default.temporaryDirectory.appendingPathComponent("merlin-workspace-create-\(UUID().uuidString)", isDirectory: true)
        defer { try? FileManager.default.removeItem(at: root) }
        try FileManager.default.createDirectory(at: root, withIntermediateDirectories: true)
        let store = WorkspaceStore()

        XCTAssertThrowsError(try WorkspaceStore.validatedFolderName("../unsafe")) { error in
            XCTAssertEqual(error as? WorkspaceDirectoryError, .invalidName)
        }
        let created = try store.createDirectory(named: "fresh-workspace", in: root)
        XCTAssertTrue(FileManager.default.fileExists(atPath: created.path))
        XCTAssertThrowsError(try store.createDirectory(named: "fresh-workspace", in: root)) { error in
            XCTAssertEqual(error as? WorkspaceDirectoryError, .alreadyExists)
        }
    }

    func testSessionSettingsFreezeAtSuccessfulSessionStart() {
        var state = AppState()
        state.nextSettings.model = "gpt-6-luna"
        state.nextSettings.effort = "high"
        state.nextSettings.routingMode = "deterministic"
        state.sessionDidStart()
        state.nextSettings.effort = "ultra"

        XCTAssertEqual(state.activeSettings?.effort, "high")
        XCTAssertEqual(state.activeSettings?.model, "gpt-6-luna")
        XCTAssertEqual(state.activeSettings?.routingMode, "deterministic")
        XCTAssertEqual(state.nextSettings.effort, "ultra")
    }

    func testSessionSettingsAllowCodexDefaultOrAValidatedCustomModelID() throws {
        var settings = SessionSettings()
        XCTAssertNil(try settings.requestedModelID())

        settings.model = "gpt-6-luna"
        XCTAssertEqual(try settings.requestedModelID(), "gpt-6-luna")

        for invalid in ["gpt 6", " gpt-6-luna", "gpt;rm", String(repeating: "a", count: 129)] {
            settings.model = invalid
            XCTAssertThrowsError(try settings.requestedModelID()) { error in
                XCTAssertEqual(error as? SessionSettings.ModelIDError, .invalid)
            }
        }
    }

    func testSessionStatusDecodesDeclaredHarnessAndSkillContractsForTheMap() throws {
        let status = try SessionStatus(data: [
            "completed_turns": .number(0),
            "active_skill_count": .number(1),
            "routing_mode": .string("semantic"),
            "automatic_lifecycle_changes": .string("deferred"),
            "feedback": .object(["pending": .number(0)]),
            "declared_runtime_contract": .bool(true),
            "hook_contracts": .array([
                .object([
                    "hook": .string("task_start"),
                    "permitted_mutations": .array([.string("system_prompt")]),
                    "declared_runtime_contract": .bool(true)
                ])
            ]),
            "recorded_evidence": .array([
                .object([
                    "id": .string("recorded-recovery"),
                    "title": .string("Shadowing Recovery"),
                    "kind": .string("controlled lifecycle recovery"),
                    "status": .string("promoted"),
                    "role": .string("Recovered 1/10 to 9/10 in a controlled fixture."),
                    "lifecycle": .array([.string("shadowing traced"), .string("same verifiers rerun")]),
                    "gates_passed": .number(5),
                    "gates_total": .number(5),
                    "model_evidence_level": .string("controlled_deterministic_fixture"),
                    "actual_provider_run": .bool(false),
                    "provider_native_invocation": .bool(false),
                    "source_path": .string("experiments/result.json"),
                    "source_sha256": .string(String(repeating: "a", count: 64))
                ])
            ]),
            "skill_contracts": .array([
                .object([
                    "id": .string("report-writer"),
                    "name": .string("Report writer"),
                    "status": .string("active"),
                    "description": .string("Writes a report."),
                    "trigger": .string("write report"),
                    "version": .number(2),
                    "validators": .array([.string("file_exists")]),
                    "step_count": .number(2),
                    "edge_count": .number(1),
                    "expected_artifacts": .array([.string("report.txt")]),
                    "failure_modes": .array([.string("missing input")])
                ])
            ])
        ])

        let graph = HarnessMapGraphModel(status: status, evidence: HarnessMapState())
        XCTAssertTrue(status.declaredRuntimeContract)
        XCTAssertEqual(graph.hooks.map(\.hook), ["task_start"])
        XCTAssertEqual(graph.skills.first?.status, "active")
        XCTAssertEqual(graph.skills.first?.stepCount, 2)
        XCTAssertEqual(graph.recordedEvidence.first?.title, "Shadowing Recovery")
        XCTAssertFalse(try XCTUnwrap(graph.recordedEvidence.first).actualProviderRun)
        XCTAssertTrue(graph.observedNodes.isEmpty)

        let layout = HarnessGraphLayout(model: graph)
        XCTAssertEqual(layout.hooks.count, 1)
        XCTAssertEqual(layout.skills.count, 1)
        XCTAssertEqual(layout.lifecycle.count, HarnessMapGraphModel.lifecycleStages.count)
        XCTAssertEqual(
            graph.matchingSkills(enabledStatuses: Set(["active"]), query: "report").map(\.id),
            ["report-writer"]
        )
    }

    @MainActor
    func testAccountModelCatalogPopulatesPickerAndAlignsReasoningEffort() async throws {
        let bridge = FlowBridgeTransport()
        let viewModel = try flowViewModel(bridge: bridge)
        viewModel.state.account = try AccountStatus(data: [
            "state": .string("connected"),
            "connected": .bool(true)
        ])
        viewModel.state.nextSettings.effort = "ultra"

        await viewModel.refreshAccountModels()

        XCTAssertTrue(viewModel.accountModelsAvailable)
        XCTAssertEqual(viewModel.accountModels.map(\.id), ["gpt-6-luna", "gpt-5.6-terra"])
        XCTAssertEqual(viewModel.state.nextSettings.model, "")
        XCTAssertEqual(viewModel.state.nextSettings.effort, "high")
        XCTAssertEqual(viewModel.nextSupportedEfforts, ["low", "high"])

        viewModel.selectNextModel("gpt-5.6-terra")
        XCTAssertEqual(viewModel.state.nextSettings.effort, "xhigh")
        XCTAssertEqual(viewModel.nextSupportedEfforts, ["medium", "xhigh"])
        let requests = await bridge.recordedRequests()
        XCTAssertEqual(requests.first?.command, .accountModels)
    }

    func testCliMissingStateReturnsToTheAccountConnectionSurface() throws {
        var state = AppState()
        state.applyAccount(try AccountStatus(data: [
            "state": .string("cli_missing"),
            "connected": .bool(false)
        ]))

        XCTAssertEqual(state.phase, .loggedOut)
    }

    func testLocalSessionHistoryCapsActualEntriesWithoutRelativeTimeMetadata() throws {
        let suite = "MerlinMacTests.session-history-\(UUID().uuidString)"
        let defaults = try XCTUnwrap(UserDefaults(suiteName: suite))
        defer { defaults.removePersistentDomain(forName: suite) }
        let store = LocalSessionHistoryStore(defaults: defaults)
        let base = Date(timeIntervalSinceReferenceDate: 1_000)

        store.record(workspace: URL(fileURLWithPath: "/tmp/one"), title: "One", date: base)
        store.record(workspace: URL(fileURLWithPath: "/tmp/two"), title: "Two", date: base.addingTimeInterval(1))
        store.record(workspace: URL(fileURLWithPath: "/tmp/three"), title: "Three", date: base.addingTimeInterval(2))
        store.record(workspace: URL(fileURLWithPath: "/tmp/four"), title: "Four", date: base.addingTimeInterval(3))
        store.record(workspace: URL(fileURLWithPath: "/tmp/two"), title: "Two again", date: base.addingTimeInterval(4))

        XCTAssertEqual(store.recentEntries().map(\.workspacePath), ["/tmp/two", "/tmp/four", "/tmp/three", "/tmp/two", "/tmp/one"])
        XCTAssertEqual(store.recentEntries().first?.title, "Two again")
    }

    func testProjectAliasAndRemovalNeverRenameOrDeleteTheFolder() throws {
        let suite = "MerlinMacTests.project-alias-\(UUID().uuidString)"
        let defaults = try XCTUnwrap(UserDefaults(suiteName: suite))
        defer { defaults.removePersistentDomain(forName: suite) }
        let root = FileManager.default.temporaryDirectory.appendingPathComponent("merlin-project-alias-\(UUID().uuidString)", isDirectory: true)
        defer { try? FileManager.default.removeItem(at: root) }
        let project = root.appendingPathComponent("original-folder", isDirectory: true)
        try FileManager.default.createDirectory(at: project, withIntermediateDirectories: true)
        let store = WorkspaceStore(defaults: defaults, applicationSupportDirectory: root)
        store.addProject(project)

        XCTAssertTrue(store.renameProjectAlias(project, to: "Presentation name"))
        XCTAssertEqual(store.projectDisplayName(for: project), "Presentation name")
        XCTAssertTrue(FileManager.default.fileExists(atPath: project.path))
        XCTAssertTrue(store.removeProject(project))
        XCTAssertTrue(store.projectDirectories().isEmpty)
        XCTAssertTrue(FileManager.default.fileExists(atPath: project.path))
    }

    func testChatHistoryRenameMoveDeleteAndSnapshotStayLocal() throws {
        let suite = "MerlinMacTests.chat-history-edit-\(UUID().uuidString)"
        let defaults = try XCTUnwrap(UserDefaults(suiteName: suite))
        defer { defaults.removePersistentDomain(forName: suite) }
        let store = LocalSessionHistoryStore(defaults: defaults)
        let project = URL(fileURLWithPath: "/tmp/merlin-history-project", isDirectory: true)
        let id = store.record(
            workspace: URL(fileURLWithPath: "/tmp/merlin-history-general", isDirectory: true),
            title: "First prompt",
            transcript: [
                LocalChatTranscriptItem(role: "user", text: "Hello"),
                LocalChatTranscriptItem(role: "assistant", text: "Hi")
            ],
            providerThreadID: "stored-only"
        )
        XCTAssertTrue(store.renameEntry(id: id, to: "Renamed chat"))
        XCTAssertTrue(store.moveEntry(id: id, toProject: project))
        XCTAssertTrue(store.updateSnapshot(
            id: id,
            transcript: [LocalChatTranscriptItem(role: "assistant", text: "Updated")],
            providerThreadID: "stored-only"
        ))
        let moved = try XCTUnwrap(store.recentEntries().first)
        XCTAssertEqual(moved.title, "Renamed chat")
        XCTAssertTrue(moved.isProject)
        XCTAssertEqual(moved.workspacePath, project.standardizedFileURL.path)
        XCTAssertTrue(moved.isRestorable)
        XCTAssertEqual(moved.transcript?.first?.text, "Updated")
        XCTAssertTrue(store.removeEntry(id: id))
        XCTAssertTrue(store.recentEntries().isEmpty)
    }

    @MainActor
    func testRestoringAChatSnapshotPreparesProviderResumeAndKeepsComposerReady() async throws {
        let suite = "MerlinMacTests.chat-restore-\(UUID().uuidString)"
        let defaults = try XCTUnwrap(UserDefaults(suiteName: suite))
        defer { defaults.removePersistentDomain(forName: suite) }
        let historyStore = LocalSessionHistoryStore(defaults: defaults)
        let workspace = FileManager.default.temporaryDirectory
            .appendingPathComponent("merlin-restored-\(UUID().uuidString)", isDirectory: true)
        try FileManager.default.createDirectory(at: workspace, withIntermediateDirectories: true)
        defer { try? FileManager.default.removeItem(at: workspace) }
        let id = historyStore.record(
            workspace: workspace,
            title: "Saved chat",
            transcript: [
                LocalChatTranscriptItem(role: "user", text: "Saved question"),
                LocalChatTranscriptItem(role: "assistant", text: "Saved answer")
            ],
            providerThreadID: "thread-retained-1"
        )
        let entry = try XCTUnwrap(historyStore.recentEntries().first(where: { $0.id == id }))
        let bridge = FlowBridgeTransport()
        let viewModel = try flowViewModel(bridge: bridge, sessionHistoryStore: historyStore)
        viewModel.state.account = try AccountStatus(data: [
            "state": .string("connected"),
            "connected": .bool(true)
        ])
        viewModel.state.sessionDidStart()

        await viewModel.restoreChat(entry)

        XCTAssertFalse(viewModel.isViewingRestoredChat)
        XCTAssertEqual(viewModel.state.messages.map(\.text), ["Saved question", "Saved answer"])
        XCTAssertEqual(viewModel.state.phase, .chatReady)
        let requests = await bridge.recordedRequests()
        XCTAssertTrue(requests.contains { $0.command == .sessionRestart })
        XCTAssertEqual(
            requests.first(where: { $0.command == .sessionResumeThread })?.payload["thread_id"]?.string,
            "thread-retained-1"
        )
    }

    @MainActor
    func testAccountContinueStartsAnAppManagedGeneralChatWithoutWorkspaceSelection() async throws {
        let bridge = FlowBridgeTransport()
        let viewModel = try flowViewModel(bridge: bridge)
        viewModel.state.account = try AccountStatus(data: [
            "state": .string("connected"),
            "connected": .bool(true)
        ])
        viewModel.state.phase = .connected
        viewModel.showHarnessMap = true
        viewModel.showDrawer = true
        viewModel.selectedHarnessNodeID = "skill-1"

        await viewModel.startGeneralChat()

        XCTAssertEqual(viewModel.state.phase, .chatReady)
        XCTAssertEqual(viewModel.state.workspace?.lastPathComponent, WorkspaceStore.generalWorkspaceName)
        XCTAssertFalse(viewModel.currentWorkspaceIsProject)
        XCTAssertFalse(viewModel.showHarnessMap)
        XCTAssertFalse(viewModel.showDrawer)
        XCTAssertNil(viewModel.selectedHarnessNodeID)
        let requests = await bridge.recordedRequests()
        XCTAssertEqual(requests.first?.command, .sessionStart)
    }

    func testManagedGeneralWorkspaceAndProjectsRemainSeparate() throws {
        let suite = "MerlinMacTests.projects-\(UUID().uuidString)"
        let defaults = try XCTUnwrap(UserDefaults(suiteName: suite))
        defer { defaults.removePersistentDomain(forName: suite) }
        let root = FileManager.default.temporaryDirectory.appendingPathComponent("merlin-general-\(UUID().uuidString)", isDirectory: true)
        defer { try? FileManager.default.removeItem(at: root) }
        try FileManager.default.createDirectory(at: root, withIntermediateDirectories: true)
        let store = WorkspaceStore(defaults: defaults, applicationSupportDirectory: root)

        let general = try store.managedGeneralWorkspace()
        XCTAssertEqual(try store.managedGeneralWorkspace(), general)
        XCTAssertTrue(FileManager.default.fileExists(atPath: general.path))
        XCTAssertTrue(store.projectDirectories().isEmpty)
        store.addProject(general)
        XCTAssertTrue(store.projectDirectories().isEmpty)

        let external = root.appendingPathComponent("external-project", isDirectory: true)
        try FileManager.default.createDirectory(at: external, withIntermediateDirectories: true)
        store.addProject(external)
        XCTAssertEqual(store.projectDirectories().map(\.path), [external.standardizedFileURL.path])
    }

    @MainActor
    func testSuccessfulSessionStartEntersChatAndResetsTransientSurfaces() async throws {
        let bridge = FlowBridgeTransport()
        let viewModel = try flowViewModel(bridge: bridge)
        viewModel.state.workspace = URL(fileURLWithPath: "/tmp/merlin-flow-workspace")
        viewModel.state.phase = .workspaceSelection
        viewModel.showHarnessMap = true
        viewModel.showDrawer = true
        viewModel.selectedHarnessNodeID = "turn-1"

        await viewModel.startSession()

        XCTAssertEqual(viewModel.state.phase, .chatReady)
        XCTAssertFalse(viewModel.showHarnessMap)
        XCTAssertFalse(viewModel.showDrawer)
        XCTAssertNil(viewModel.selectedHarnessNodeID)
        XCTAssertEqual(viewModel.state.sessionStatus?.completedTurns, 0)
        let requests = await bridge.recordedRequests()
        XCTAssertNil(requests.first?.payload["model"])
    }

    @MainActor
    func testNewProviderThreadKeepsChatActiveAndResetsTransientSurfaces() async throws {
        let viewModel = try flowViewModel()
        viewModel.state.sessionDidStart()
        viewModel.showHarnessMap = true
        viewModel.showDrawer = true
        viewModel.selectedHarnessNodeID = "feedback-1"

        await viewModel.startNewThread()

        XCTAssertEqual(viewModel.state.phase, .chatReady)
        XCTAssertFalse(viewModel.showHarnessMap)
        XCTAssertFalse(viewModel.showDrawer)
        XCTAssertNil(viewModel.selectedHarnessNodeID)
    }

    @MainActor
    func testNewChatUpdatesSettingsInPlaceBeforeStartingANewProviderThread() async throws {
        let bridge = FlowBridgeTransport()
        let viewModel = try flowViewModel(bridge: bridge)
        viewModel.state.account = try AccountStatus(data: [
            "state": .string("connected"),
            "connected": .bool(true)
        ])
        viewModel.state.workspace = URL(fileURLWithPath: "/tmp/merlin-restart-workspace")
        viewModel.state.sessionDidStart()
        viewModel.state.nextSettings.model = "gpt-6-luna"
        viewModel.showHarnessMap = true
        viewModel.showDrawer = true

        await viewModel.startNewChat()

        XCTAssertEqual(viewModel.state.phase, .chatReady)
        XCTAssertEqual(viewModel.state.activeSettings?.model, "gpt-6-luna")
        XCTAssertFalse(viewModel.showHarnessMap)
        XCTAssertFalse(viewModel.showDrawer)
        let requests = await bridge.recordedRequests()
        XCTAssertEqual(requests.first?.command, .sessionUpdateSettings)
        XCTAssertEqual(requests.first?.payload["model"]?.string, "gpt-6-luna")
        XCTAssertTrue(requests.contains { $0.command == .sessionNewThread })
        XCTAssertFalse(requests.contains { $0.command == .sessionRestart })
    }

    @MainActor
    func testProviderEvidenceBecomesVerifiedOnlyAfterCompletedTurn() throws {
        var state = AppState()
        state.sessionDidStart()
        XCTAssertFalse(state.providerTurnVerified)

        state.completeTurn(try TurnDetails(data: [
            "answer": .string("Done."),
            "thread_id": .string("thread-1"),
            "turn_number": .number(1),
            "provisioned_skills": .array([]),
            "routing_decision": .object([
                "routing_source": .string("semantic_abstain"),
                "candidate_skill_count": .number(0),
                "final_provisioned_ids": .array([]),
                "final_abstain_reason": .string("no_anchor_candidates"),
                "authoritative_final_decision": .bool(true)
            ]),
            "harness_actions": .array([]),
            "evidence": .object([
                "raw_trace_pointer": .string("turn-0001.jsonl"),
                "prompt_provisioning_is_native_invocation": .bool(false)
            ])
        ]))

        XCTAssertTrue(state.providerTurnVerified)
    }

    @MainActor
    func testThemeAndLanguageChangesDoNotChangeTheActiveSessionOrChatSurface() throws {
        let savedLanguage = UserDefaults.standard.object(forKey: AppLanguage.storageKey)
        let appearanceKey = "merlin.appearance"
        let savedAppearance = UserDefaults.standard.object(forKey: appearanceKey)
        defer {
            if let savedLanguage {
                UserDefaults.standard.set(savedLanguage, forKey: AppLanguage.storageKey)
            } else {
                UserDefaults.standard.removeObject(forKey: AppLanguage.storageKey)
            }
            if let savedAppearance {
                UserDefaults.standard.set(savedAppearance, forKey: appearanceKey)
            } else {
                UserDefaults.standard.removeObject(forKey: appearanceKey)
            }
        }
        UserDefaults.standard.set("dark", forKey: appearanceKey)

        let viewModel = try flowViewModel()
        let workspace = URL(fileURLWithPath: "/tmp/merlin-flow-workspace")
        viewModel.state.workspace = workspace
        viewModel.state.sessionDidStart()
        viewModel.showHarnessMap = true
        viewModel.showDrawer = true
        viewModel.selectedHarnessNodeID = "selected-node"
        let activeSettings = viewModel.state.activeSettings

        viewModel.toggleAppearance()
        viewModel.setLanguage(.korean)

        XCTAssertEqual(viewModel.preferredColorScheme, .light)
        XCTAssertEqual(viewModel.state.phase, .chatReady)
        XCTAssertEqual(viewModel.state.workspace, workspace)
        XCTAssertEqual(viewModel.state.activeSettings, activeSettings)
        XCTAssertTrue(viewModel.showHarnessMap)
        XCTAssertTrue(viewModel.showDrawer)
        XCTAssertEqual(viewModel.selectedHarnessNodeID, "selected-node")
    }

    func testSlashCommandParserKeepsProviderSkillCommandsDistinct() {
        XCTAssertEqual(
            SlashCommand.parse("/create-skill parse project logs"),
            SlashCommandInvocation(command: .createSkill, arguments: "parse project logs")
        )
        XCTAssertEqual(
            SlashCommand.parse("/skill summarize-tests run the focused suite"),
            SlashCommandInvocation(command: .skill, arguments: "summarize-tests run the focused suite")
        )
        XCTAssertTrue(SlashCommand.skill.forwardsToProvider)
        XCTAssertEqual(SlashCommand.suggestions(for: "/s"), [.skills, .skill])
    }

    @MainActor
    func testNaturalLanguageShadowingEvidenceOpensVerifiedRecordWithoutProviderCall() async throws {
        let bridge = FlowBridgeTransport()
        let viewModel = try flowViewModel(bridge: bridge)
        viewModel.state.sessionDidStart()
        viewModel.state.workspace = URL(fileURLWithPath: "/tmp/merlin-recorded-evidence-chat", isDirectory: true)
        viewModel.state.sessionStatus = try SessionStatus(data: [
            "completed_turns": .number(0),
            "active_skill_count": .number(0),
            "routing_mode": .string("semantic"),
            "automatic_lifecycle_changes": .string("deferred"),
            "feedback": .object(["pending": .number(0)]),
            "recorded_evidence": .array([
                .object([
                    "id": .string("recorded-controlled-shadowing-recovery-v1"),
                    "title": .string("Shadowing Recovery"),
                    "kind": .string("controlled lifecycle recovery"),
                    "status": .string("promoted"),
                    "role": .string("Recovered 1/10 to 9/10 in a controlled fixture."),
                    "lifecycle": .array([.string("same verifiers rerun")]),
                    "gates_passed": .number(5),
                    "gates_total": .number(5),
                    "model_evidence_level": .string("controlled_deterministic_fixture"),
                    "actual_provider_run": .bool(false),
                    "provider_native_invocation": .bool(false),
                    "source_path": .string("experiments/mvp/results/lifecycle_recovery/summary.json"),
                    "source_sha256": .string(String(repeating: "a", count: 64))
                ])
            ])
        ])

        viewModel.draft = "Show me the verified shadowing recovery result and its evidence boundary."
        await viewModel.sendDraft()

        XCTAssertFalse(viewModel.showHarnessMap)
        XCTAssertEqual(viewModel.harnessMapMode, .evidence)
        XCTAssertEqual(viewModel.state.phase, .chatReady)
        XCTAssertFalse(viewModel.state.providerTurnVerified)
        XCTAssertEqual(viewModel.state.messages.map(\.kind), [.user, .assistant])
        let response = try XCTUnwrap(viewModel.state.messages.last?.text)
        XCTAssertTrue(response.contains("1/10") && response.contains("9/10"))
        XCTAssertTrue(response.contains("89%") && response.contains("0%"))
        XCTAssertTrue(response.contains("provider") && response.contains("native"))
        XCTAssertEqual(viewModel.recentSessionHistory.count, 1)
        XCTAssertNil(viewModel.recentSessionHistory.first?.providerThreadID)
        let requests = await bridge.recordedRequests()
        XCTAssertTrue(requests.isEmpty)
    }

    @MainActor
    func testSlashSkillsNavigatesLocallyAndCreateSkillAddsAnHonestProposal() async throws {
        let bridge = FlowBridgeTransport()
        let viewModel = try flowViewModel(bridge: bridge)
        viewModel.state.sessionDidStart()

        viewModel.draft = "/skills"
        await viewModel.sendDraft()

        XCTAssertTrue(viewModel.showHarnessMap)
        XCTAssertEqual(viewModel.harnessMapMode, .skills)
        XCTAssertTrue(viewModel.state.messages.isEmpty)
        let initialRequests = await bridge.recordedRequests()
        XCTAssertTrue(initialRequests.isEmpty)

        viewModel.draft = "/create-skill extract build diagnostics"
        await viewModel.sendDraft()

        let proposal = try XCTUnwrap(viewModel.state.messages.last?.commandProposal)
        XCTAssertEqual(proposal.command, .createSkill)
        XCTAssertNotNil(proposal.suggestedPrompt)
        let finalRequests = await bridge.recordedRequests()
        XCTAssertTrue(finalRequests.isEmpty)
    }

    @MainActor
    func testSlashRejectUsesTheExistingApprovalBridgeOnlyWhenPending() async throws {
        let bridge = FlowBridgeTransport()
        let viewModel = try flowViewModel(bridge: bridge)
        viewModel.state.sessionDidStart()
        let approval = try ApprovalRequest(data: [
            "message": .string("Approval is required."),
            "proposal": .object([
                "capability_id": .string("registered-skill"),
                "planned_mutations": .array([])
            ])
        ])
        viewModel.state.requireApproval(approval)

        viewModel.draft = "/reject"
        await viewModel.sendDraft()

        XCTAssertEqual(viewModel.state.phase, .chatReady)
        let requests = await bridge.recordedRequests()
        XCTAssertEqual(requests.map(\.command), [.approvalResolve])
        XCTAssertTrue(viewModel.state.messages.contains { $0.text.contains("/reject") })
    }

    @MainActor
    func testAccountConnectionSheetRefreshPreservesActiveChat() async throws {
        let bridge = FlowBridgeTransport()
        let viewModel = try flowViewModel(bridge: bridge)
        viewModel.state.workspace = URL(fileURLWithPath: "/tmp/merlin-active-chat")
        viewModel.state.sessionDidStart()
        viewModel.state.messages.append(ChatMessage(kind: .assistant, text: "Keep this chat visible."))
        viewModel.showDrawer = true
        viewModel.showHarnessMap = true
        viewModel.selectedHarnessNodeID = "active-node"
        let activeSettings = viewModel.state.activeSettings

        viewModel.openAccountConnectionSheet()

        XCTAssertTrue(viewModel.showAccountConnectionSheet)
        XCTAssertEqual(viewModel.state.phase, .chatReady)
        let requestsOnEntry = await bridge.recordedRequests()
        XCTAssertTrue(requestsOnEntry.isEmpty)

        await viewModel.refreshAccountStatusPreservingSession()

        XCTAssertEqual(viewModel.state.phase, .chatReady)
        XCTAssertEqual(viewModel.state.activeSettings, activeSettings)
        XCTAssertEqual(viewModel.state.messages.last?.text, "Keep this chat visible.")
        XCTAssertTrue(viewModel.showDrawer)
        XCTAssertTrue(viewModel.showHarnessMap)
        XCTAssertEqual(viewModel.selectedHarnessNodeID, "active-node")
        XCTAssertTrue(viewModel.state.account?.connected == true)
        XCTAssertNil(viewModel.accountConnectionError)

        let requests = await bridge.recordedRequests()
        XCTAssertEqual(requests.map(\.command), [.accountStatus])
    }

    private func data(_ response: BridgeResponse) throws -> [String: BridgeValue] {
        guard let data = response.data else { throw BridgeProtocolError.missingData }
        return data
    }

    @MainActor
    private func flowViewModel(
        bridge: any BridgeTransport = FlowBridgeTransport(),
        sessionHistoryStore: LocalSessionHistoryStore? = nil
    ) throws -> MerlinViewModel {
        let suite = "MerlinMacTests.flow-\(UUID().uuidString)"
        let defaults = try XCTUnwrap(UserDefaults(suiteName: suite))
        addTeardownBlock { defaults.removePersistentDomain(forName: suite) }
        let supportDirectory = FileManager.default.temporaryDirectory
            .appendingPathComponent("merlin-flow-support-\(UUID().uuidString)", isDirectory: true)
        try FileManager.default.createDirectory(at: supportDirectory, withIntermediateDirectories: true)
        addTeardownBlock { try? FileManager.default.removeItem(at: supportDirectory) }
        return MerlinViewModel(
            bridge: bridge,
            workspaceStore: WorkspaceStore(defaults: defaults, applicationSupportDirectory: supportDirectory),
            sessionHistoryStore: sessionHistoryStore ?? LocalSessionHistoryStore(defaults: defaults)
        )
    }
}

private struct RecordedBridgeRequest: Sendable {
    let command: BridgeCommand
    let payload: [String: BridgeValue]
}

private actor FlowBridgeTransport: BridgeTransport {
    private var requests: [RecordedBridgeRequest] = []

    func request(command: BridgeCommand, payload: [String: BridgeValue]) async throws -> BridgeResponse {
        requests.append(RecordedBridgeRequest(command: command, payload: payload))
        switch command {
        case .accountStatus:
            return response(
                event: "account.status",
                data: [
                    "state": .string("connected"),
                    "connected": .bool(true),
                    "executable": .string("/usr/local/bin/codex"),
                    "cli_version": .string("0.106.0"),
                    "auth_method": .string("chatgpt")
                ]
            )
        case .accountModels:
            return response(
                event: "account.models",
                data: [
                    "available": .bool(true),
                    "models": .array([
                        .object([
                            "id": .string("gpt-6-luna"),
                            "display_name": .string("Luna"),
                            "description": .string("Account default"),
                            "is_default": .bool(true),
                            "default_effort": .string("high"),
                            "supported_efforts": .array([.string("low"), .string("high")])
                        ]),
                        .object([
                            "id": .string("gpt-5.6-terra"),
                            "display_name": .string("Terra"),
                            "description": .string("Account model"),
                            "is_default": .bool(false),
                            "default_effort": .string("xhigh"),
                            "supported_efforts": .array([.string("medium"), .string("xhigh")])
                        ])
                    ])
                ]
            )
        case .sessionStart:
            return response(event: "session.started")
        case .sessionRestart:
            return response(event: "session.restarted")
        case .sessionNewThread:
            return response(event: "session.new_thread")
        case .sessionResumeThread:
            return response(
                event: "session.resume_thread",
                data: [
                    "prepared": .bool(true),
                    "provider_resume_verified": .bool(false)
                ]
            )
        case .approvalResolve:
            return response(event: "approval.declined")
        case .sessionUpdateSettings:
            return response(event: "session.settings_updated")
        case .sessionStatus:
            return response(
                event: "session.status",
                data: [
                    "completed_turns": .number(0),
                    "active_skill_count": .number(0),
                    "routing_mode": .string("semantic"),
                    "automatic_lifecycle_changes": .string("deferred"),
                    "feedback": .object(["pending": .number(0)])
                ]
            )
        default:
            throw BridgeProtocolError.unsupportedEvent(command.rawValue)
        }
    }

    func shutdown() async {}

    func recordedRequests() -> [RecordedBridgeRequest] { requests }

    private func response(event: String, data: [String: BridgeValue]? = nil) -> BridgeResponse {
        BridgeResponse(
            schemaVersion: 1,
            requestID: nil,
            ok: true,
            event: event,
            data: data,
            error: nil
        )
    }
}

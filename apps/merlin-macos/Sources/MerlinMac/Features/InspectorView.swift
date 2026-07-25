import SwiftUI

struct SessionDrawerView: View {
    @ObservedObject var viewModel: MerlinViewModel

    var body: some View {
        let c = viewModel.copy
        let active = viewModel.state.activeSettings ?? viewModel.state.nextSettings
        VStack(spacing: 0) {
            ScrollView {
                VStack(alignment: .leading, spacing: 16) {
                HStack {
                    Text(c.session.uppercased()).font(.system(size: 11, weight: .medium)).foregroundStyle(MerlinTheme.muted)
                    Spacer()
                    Button { viewModel.showDrawer = false } label: {
                        Image(systemName: "xmark").font(.system(size: 11, weight: .medium)).frame(width: 24, height: 24)
                    }
                    .buttonStyle(.plain).foregroundStyle(MerlinTheme.muted)
                }

                DrawerBlock(title: c.workspace) {
                    Text(viewModel.headerWorkspaceLabel)
                        .font(.system(size: 12, weight: .medium)).foregroundStyle(MerlinTheme.text2)
                }

                DrawerBlock(title: c.session) {
                    DrawerLine(label: c.model, value: c.modelLabel(active.model))
                    DrawerLine(label: c.effort, value: active.effort)
                    DrawerLine(label: c.routing, value: active.routingMode)
                    DrawerLine(label: c.autonomy, value: active.autonomyMode)
                }

                DrawerBlock(title: c.text("Next session settings", "다음 세션 설정")) {
                    Text(c.nextSessionOnly).font(.system(size: 11)).foregroundStyle(MerlinTheme.muted)
                    DrawerSettingLabel(text: c.model)
                    if !viewModel.accountModels.isEmpty {
                        Picker(c.text("Account models", "계정 모델"), selection: Binding(
                            get: { viewModel.state.nextSettings.model },
                            set: { viewModel.selectNextModel($0) }
                        )) {
                            Text(c.codexDefaultModel).tag("")
                            ForEach(viewModel.accountModels) { model in
                                Text(model.displayName == model.id ? model.id : "\(model.displayName) (\(model.id))")
                                    .tag(model.id)
                            }
                        }
                        .pickerStyle(.menu)
                        .labelsHidden()
                        .frame(maxWidth: .infinity, alignment: .leading)
                        .accessibilityLabel(c.text("Account models", "계정 모델"))
                        if let description = viewModel.selectedNextAccountModel?.description,
                           !description.isEmpty {
                            Text(description)
                                .font(.system(size: 10.5))
                                .foregroundStyle(MerlinTheme.muted)
                                .lineLimit(1)
                        }
                    } else {
                        TextField(c.text("Codex default (optional custom model ID)", "Codex 기본값(선택 사용자 모델 ID)"), text: Binding(
                            get: { viewModel.state.nextSettings.model },
                            set: { viewModel.selectNextModel($0) }
                        ))
                            .textFieldStyle(.plain)
                            .font(.system(size: 11.5, design: .monospaced))
                            .padding(.horizontal, 9).frame(height: 30)
                            .background(MerlinTheme.bg, in: RoundedRectangle(cornerRadius: 7, style: .continuous))
                            .overlay { RoundedRectangle(cornerRadius: 7).stroke(MerlinTheme.border) }
                            .accessibilityLabel(c.model)
                        Text(c.text("Account model discovery is unavailable; leave blank for Codex default.", "계정 모델 조회를 사용할 수 없습니다. 비워 두면 Codex 기본값을 사용합니다."))
                            .font(.system(size: 10.5))
                            .foregroundStyle(MerlinTheme.muted)
                            .lineSpacing(2)
                    }
                    if let message = viewModel.nextModelValidationMessage {
                        Text(message).font(.system(size: 10.5)).foregroundStyle(MerlinTheme.red).lineSpacing(2)
                    }
                    DrawerSettingLabel(text: c.effort)
                    DrawerSegmentedRow(
                        values: viewModel.nextSupportedEfforts,
                        selection: $viewModel.state.nextSettings.effort
                    )
                    DrawerSettingLabel(text: c.routing)
                    DrawerSegmentedRow(values: ["semantic", "deterministic"], selection: $viewModel.state.nextSettings.routingMode)
                    DrawerSettingLabel(text: c.autonomy)
                    DrawerSegmentedRow(values: ["managed", "strict"], selection: $viewModel.state.nextSettings.autonomyMode)
                    Text(c.text("These controls do not mutate the active provider session.", "이 설정은 현재 공급자 세션을 변경하지 않습니다."))
                        .font(.system(size: 10.5)).foregroundStyle(MerlinTheme.muted).lineSpacing(2)
                }

                DrawerBlock(title: c.text("Connection evidence", "연결 증거")) {
                    DrawerLine(label: "CLI", value: viewModel.cliEvidenceText(copy: c))
                    DrawerLine(label: "Provider", value: viewModel.providerEvidenceText(copy: c))
                    Text(c.text("CLI authentication proves local account access. Provider verification appears only after this app receives a completed chat turn.", "CLI 인증은 로컬 계정 접근만 증명합니다. 공급자 확인은 이 앱이 완료된 채팅 턴을 받은 뒤에만 표시됩니다."))
                        .font(.system(size: 10.5)).foregroundStyle(MerlinTheme.muted).lineSpacing(2)
                }

                DrawerBlock(title: c.text("Provisioned skills · latest turn", "프로비저닝된 스킬 · 최근 턴")) {
                    if let skills = viewModel.state.lastTurn?.provisionedSkills, !skills.isEmpty {
                        ForEach(skills) { skill in
                            ProvisionedSkillDiagnosticRow(skill: skill)
                        }
                        Text(c.text(
                            "Shown skills were provisioned to the model. This is selection evidence, not provider-native invocation evidence.",
                            "표시된 스킬은 모델에 프로비저닝된 것입니다. 이는 선택 증거이며 공급자 네이티브 호출 증거가 아닙니다."
                        ))
                        .font(.system(size: 10.5))
                        .foregroundStyle(MerlinTheme.muted)
                        .lineSpacing(2)
                        .fixedSize(horizontal: false, vertical: true)
                    } else {
                        DrawerEmptyText(c.text("No skill was returned for the latest completed turn.", "최근 완료된 턴에서 반환된 스킬이 없습니다."))
                    }
                }

                DrawerBlock(title: c.text("Trace pointer", "트레이스 포인터")) {
                    Text(viewModel.state.lastTurn?.rawTracePointer ?? c.text("No completed turn trace.", "완료된 턴 트레이스가 없습니다."))
                        .font(.system(size: 11, design: .monospaced)).foregroundStyle(MerlinTheme.text2).textSelection(.enabled)
                    Text(c.text("Immutable per-turn evidence pointer. Exposure does not imply provider-native skill invocation.", "턴별 불변 증거 포인터입니다. 노출은 공급자 네이티브 스킬 호출을 뜻하지 않습니다."))
                        .font(.system(size: 10.5)).foregroundStyle(MerlinTheme.muted).lineSpacing(2)
                }

                DrawerBlock(title: c.text("Harness actions", "하네스 작업")) {
                    if let actions = viewModel.state.lastTurn?.harnessActions, !actions.isEmpty {
                        ForEach(actions) { action in
                            VStack(alignment: .leading, spacing: 3) {
                                Text(action.status).font(.system(size: 12.5, weight: .medium))
                                if let scope = action.scope { Text(scope).font(.system(size: 11.5)).foregroundStyle(MerlinTheme.muted) }
                                if let risk = action.riskClass {
                                    Text("\(risk) · \(action.gateCount ?? 0) gates")
                                        .font(.system(size: 11, design: .monospaced)).foregroundStyle(MerlinTheme.muted)
                                }
                            }
                        }
                    } else {
                        DrawerEmptyText(c.text("No harness action was returned for the latest turn.", "최근 턴에서 반환된 하네스 작업이 없습니다."))
                    }
                }

                DrawerBlock(title: c.text("Observed session status", "관찰된 세션 상태")) {
                    if let status = viewModel.state.sessionStatus {
                        DrawerLine(label: c.text("Completed turns", "완료된 턴"), value: "\(status.completedTurns)")
                        DrawerLine(label: c.text("Active skills", "활성 스킬"), value: "\(status.activeSkillCount)")
                        DrawerLine(label: c.text("Lifecycle", "수명주기"), value: status.automaticLifecycleChanges)
                        DrawerLine(label: c.text("Feedback pending", "대기 중 피드백"), value: "\(status.feedbackPending)")
                    } else {
                        DrawerEmptyText(c.text("No status response yet.", "아직 상태 응답이 없습니다."))
                    }
                    Button(c.refreshStatus) { Task { await viewModel.refreshSessionStatus() } }
                        .buttonStyle(ReferenceButtonStyle(tone: .secondary))
                }

                GovernanceBlock(viewModel: viewModel)
                }
                .padding(.horizontal, 16).padding(.top, 12).padding(.bottom, 16)
            }
            Divider().overlay(MerlinTheme.border)
            Button(c.text("Save settings", "설정 저장")) {
                Task { await viewModel.saveAndApplyNextSessionSettings() }
            }
            .buttonStyle(ReferenceButtonStyle(tone: .primary))
            .disabled(viewModel.nextModelValidationMessage != nil || viewModel.state.phase != .chatReady)
            .padding(16)
        }
        .background(MerlinTheme.panel)
    }
}

/// The self-management surface: campaign standing, the invocation-evidence
/// gate, the evolution ledger, and every lifecycle operation with the reason it
/// is or is not currently available.
///
/// This block is deliberately read-only. Repair, merge, hide and retirement are
/// evaluator-backed batch campaigns, and promotion is gated on provider-native
/// invocation evidence — offering buttons for them here would manufacture a
/// decision the harness has not earned.
private struct GovernanceBlock: View {
    @ObservedObject var viewModel: MerlinViewModel

    var body: some View {
        let c = viewModel.copy
        DrawerBlock(title: c.text("Self-management", "자기관리")) {
            if let governance = viewModel.governance {
                campaign(governance.campaign, c: c)
                invocation(governance.invocationEvidence, c: c)
                evolution(governance.evolution, c: c)
                lifecycle(governance.lifecycleOperations, c: c)
            } else if let error = viewModel.governanceError {
                Text(error).font(.system(size: 11)).foregroundStyle(MerlinTheme.red).lineSpacing(2)
            } else {
                DrawerEmptyText(c.text("Governance state has not been read yet.", "거버넌스 상태를 아직 읽지 않았습니다."))
            }
            Button(c.text("Refresh governance", "거버넌스 새로고침")) {
                Task { await viewModel.refreshGovernance() }
            }
            .buttonStyle(ReferenceButtonStyle(tone: .secondary))
        }
        .task {
            // Read once when the drawer first appears; the button re-reads.
            if viewModel.governance == nil && viewModel.governanceError == nil {
                await viewModel.refreshGovernance()
            }
        }
    }

    @ViewBuilder
    private func campaign(_ state: HarnessGovernance.CampaignState, c: AppCopy) -> some View {
        if state.artifactsPresent {
            DrawerLine(
                label: c.text("Observations", "관측"),
                value: "\(state.matchedObservationCount ?? 0) / \(state.pairCount ?? 0) pairs"
            )
            DrawerLine(
                label: c.text("Task contracts", "태스크 계약"),
                value: "\(state.taskCount ?? 0)"
            )
            DrawerLine(
                label: c.text("Lifecycle changes", "수명주기 변경"),
                value: "\(state.lifecycleChangeCount ?? 0)"
            )
            DrawerLine(
                label: "G/S",
                value: state.governanceOverSavings.map { String(format: "%.3f", $0) }
                    ?? state.governanceOverSavingsStatus
                    ?? c.text("unavailable", "산출 불가")
            )
            DrawerLine(
                label: "Level 7",
                value: state.level7Achieved
                    ? c.text("achieved", "달성")
                    : (state.level7Status ?? c.text("not achieved", "미달성"))
            )
            if !state.level7Achieved && !state.unmetLevel7Checks.isEmpty {
                Text(
                    c.text("Unmet: ", "미충족: ")
                        + state.unmetLevel7Checks.joined(separator: ", ")
                )
                .font(.system(size: 10.5, design: .monospaced))
                .foregroundStyle(MerlinTheme.muted)
                .lineSpacing(2)
            }
            if let error = state.validationError {
                Text(c.text("Ledger validation failed: ", "원장 검증 실패: ") + error)
                    .font(.system(size: 10.5)).foregroundStyle(MerlinTheme.red).lineSpacing(2)
            }
        } else {
            DrawerEmptyText(c.text("No campaign artifacts on disk.", "디스크에 캠페인 아티팩트가 없습니다."))
        }
    }

    @ViewBuilder
    private func invocation(_ state: HarnessGovernance.InvocationEvidence, c: AppCopy) -> some View {
        DrawerLine(
            label: c.text("Invocation evidence", "호출 증거"),
            value: state.providerNativeEvidenceComplete
                ? c.text("complete", "완료")
                : c.text("incomplete", "미완료")
        )
        if let reason = state.blockingReason {
            Text(reason)
                .font(.system(size: 10.5, design: .monospaced))
                .foregroundStyle(MerlinTheme.red)
                .lineSpacing(2)
                .textSelection(.enabled)
        }
        if let consequence = state.consequence {
            Text(consequence)
                .font(.system(size: 10.5)).foregroundStyle(MerlinTheme.muted).lineSpacing(2)
        }
    }

    @ViewBuilder
    private func evolution(_ state: HarnessGovernance.EvolutionState, c: AppCopy) -> some View {
        if state.ledgerPresent {
            DrawerLine(
                label: c.text("Evolution ledger", "진화 원장"),
                value: "\(state.observationCount ?? 0) obs · \(state.promotionCount ?? 0) promo · \(state.rollbackCount ?? 0) rollback"
            )
            if let error = state.validationError {
                Text(error).font(.system(size: 10.5)).foregroundStyle(MerlinTheme.red).lineSpacing(2)
            }
        } else {
            DrawerLine(
                label: c.text("Evolution ledger", "진화 원장"),
                value: c.text("absent", "없음")
            )
            if let reason = state.reason {
                Text(reason).font(.system(size: 10.5)).foregroundStyle(MerlinTheme.muted).lineSpacing(2)
            }
        }
    }

    @ViewBuilder
    private func lifecycle(_ operations: [HarnessGovernance.LifecycleOperation], c: AppCopy) -> some View {
        if !operations.isEmpty {
            DrawerSettingLabel(text: c.text("Lifecycle operations", "수명주기 작업"))
            ForEach(operations) { operation in
                VStack(alignment: .leading, spacing: 3) {
                    HStack(spacing: 6) {
                        Circle()
                            .fill(operation.available ? MerlinTheme.accent : MerlinTheme.muted)
                            .frame(width: 6, height: 6)
                        Text(operation.kind).font(.system(size: 12.5, weight: .medium))
                        Spacer(minLength: 8)
                        Text("\(operation.observedCount)")
                            .font(.system(size: 11, design: .monospaced))
                            .foregroundStyle(MerlinTheme.muted)
                    }
                    Text(operation.reason)
                        .font(.system(size: 10.5)).foregroundStyle(MerlinTheme.muted).lineSpacing(2)
                }
            }
            Text(c.text("Read-only. Lifecycle changes are earned by validated campaigns, not issued from this panel.", "읽기 전용입니다. 수명주기 변경은 검증된 캠페인으로만 발생하며 이 패널에서 실행하지 않습니다."))
                .font(.system(size: 10.5)).foregroundStyle(MerlinTheme.muted).lineSpacing(2)
        }
    }
}

private struct DrawerBlock<Content: View>: View {
    let title: String
    @ViewBuilder let content: Content

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            Text(title.uppercased()).font(.system(size: 11)).foregroundStyle(MerlinTheme.muted)
            content
        }
    }
}

private struct DrawerLine: View {
    let label: String
    let value: String

    var body: some View {
        HStack(alignment: .firstTextBaseline) {
            Text(label).foregroundStyle(MerlinTheme.muted)
            Spacer(minLength: 8)
            Text(value).fontWeight(.medium).multilineTextAlignment(.trailing).textSelection(.enabled)
        }
        .font(.system(size: 12))
    }
}

private struct DrawerEmptyText: View {
    let text: String
    init(_ text: String) { self.text = text }
    var body: some View { Text(text).font(.system(size: 11.5)).foregroundStyle(MerlinTheme.muted) }
}

/// Secondary, read-only diagnostics for a skill selected in the latest
/// completed turn. This stays in the session drawer so it never displaces the
/// conversation or makes prompt exposure appear to be an invocation event.
private struct ProvisionedSkillDiagnosticRow: View {
    let skill: ProvisionedSkill

    var body: some View {
        VStack(alignment: .leading, spacing: 3) {
            HStack(alignment: .firstTextBaseline, spacing: 8) {
                Text(skill.name)
                    .font(.system(size: 12.5, weight: .medium))
                    .lineLimit(1)
                Spacer(minLength: 8)
                Text(String(format: "%.3f", skill.score))
                    .font(.system(size: 10.5, design: .monospaced))
                    .foregroundStyle(MerlinTheme.text2)
                    .textSelection(.enabled)
            }
            Text(skill.why)
                .font(.system(size: 11))
                .foregroundStyle(MerlinTheme.muted)
                .lineLimit(2)
                .fixedSize(horizontal: false, vertical: true)
            Text(skill.skillID)
                .font(.system(size: 10, design: .monospaced))
                .foregroundStyle(MerlinTheme.muted)
                .lineLimit(1)
                .textSelection(.enabled)
        }
    }
}

private struct DrawerSettingLabel: View {
    let text: String
    var body: some View { Text(text).font(.system(size: 11)).foregroundStyle(MerlinTheme.muted).padding(.top, 2) }
}

private struct DrawerSegmentedRow: View {
    let values: [String]
    @Binding var selection: String

    var body: some View {
        ViewThatFits(in: .horizontal) {
            row
            VStack(spacing: 3) { row }
        }
    }

    private var row: some View {
        HStack(spacing: 2) {
            ForEach(values, id: \.self) { value in
                Button { selection = value } label: {
                    Text(value).font(.system(size: 10.5, weight: selection == value ? .medium : .regular))
                        .foregroundStyle(selection == value ? MerlinTheme.text : MerlinTheme.muted)
                        .frame(maxWidth: .infinity, minHeight: 27)
                        .background {
                            if selection == value { MerlinSelectionSurface(cornerRadius: 6) }
                        }
                }
                .buttonStyle(.plain)
            }
        }
        .padding(2)
        .background(MerlinTheme.bg, in: RoundedRectangle(cornerRadius: 8, style: .continuous))
        .overlay { RoundedRectangle(cornerRadius: 8).stroke(MerlinTheme.border) }
    }
}

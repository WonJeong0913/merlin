import SwiftUI

struct ChatShellView: View {
    @ObservedObject var viewModel: MerlinViewModel

    var body: some View {
        GeometryReader { proxy in
            let compactSidebar = proxy.size.width < 900
            let drawerInColumn = viewModel.showDrawer && proxy.size.width >= 1040
            HStack(spacing: 0) {
                SidebarView(viewModel: viewModel, compact: compactSidebar)
                    .frame(width: compactSidebar ? 68 : 236)
                Divider().overlay(MerlinTheme.border)
                Group {
                    if viewModel.showHarnessMap {
                        HarnessMapScreen(viewModel: viewModel)
                    } else {
                        ConversationView(viewModel: viewModel)
                    }
                }
                .frame(maxWidth: .infinity, maxHeight: .infinity)
                if drawerInColumn && !viewModel.showHarnessMap {
                    Divider().overlay(MerlinTheme.border)
                    SessionDrawerView(viewModel: viewModel).frame(width: 300)
                }
            }
            .sheet(isPresented: drawerSheetBinding(drawerInColumn: drawerInColumn)) {
                SessionDrawerView(viewModel: viewModel).frame(minWidth: 360, minHeight: 560)
            }
        }
        .background(MerlinTheme.bg)
    }

    private func drawerSheetBinding(drawerInColumn: Bool) -> Binding<Bool> {
        Binding(
            get: { viewModel.showDrawer && !drawerInColumn && !viewModel.showHarnessMap },
            set: { if !$0 { viewModel.showDrawer = false } }
        )
    }
}

private struct ConversationView: View {
    @ObservedObject var viewModel: MerlinViewModel
    @FocusState private var composerFocused: Bool

    var body: some View {
        let c = viewModel.copy
        VStack(spacing: 0) {
            ChatHeader(viewModel: viewModel, showsSessionButton: true)
            Divider().overlay(MerlinTheme.border)
            ScrollViewReader { scroll in
                ScrollView {
                    LazyVStack(alignment: .leading, spacing: 16) {
                        if viewModel.state.messages.isEmpty {
                            EmptyConversation(viewModel: viewModel)
                                .frame(maxWidth: .infinity, minHeight: 390)
                        } else {
                            ForEach(viewModel.state.messages) { message in
                                MessageView(
                                    message: message,
                                    isPendingApproval: viewModel.state.phase == .approvalPending && message.approval == viewModel.state.pendingApproval,
                                    viewModel: viewModel
                                )
                                .id(message.id)
                            }
                            if viewModel.state.phase == .turnRunning {
                                HStack(spacing: 8) {
                                    ProgressView().controlSize(.small)
                                    Text(c.text("Working in the selected workspace…", "선택한 작업 공간에서 작업 중…"))
                                        .font(.system(size: 13)).foregroundStyle(MerlinTheme.muted)
                                }
                                .padding(.leading, 4)
                            }
                        }
                    }
                    .frame(maxWidth: 720, alignment: .leading)
                    .padding(.horizontal, 24).padding(.vertical, 24)
                }
                .onChange(of: viewModel.state.messages.count) { _ in
                    withAnimation(.easeOut(duration: 0.18)) {
                        scroll.scrollTo(viewModel.state.messages.last?.id, anchor: .bottom)
                    }
                }
            }
        }
        .safeAreaInset(edge: .bottom, spacing: 0) {
            ComposerView(viewModel: viewModel, composerFocused: $composerFocused)
        }
    }
}

private struct ChatHeader: View {
    @ObservedObject var viewModel: MerlinViewModel
    let showsSessionButton: Bool

    var body: some View {
        let c = viewModel.copy
        let active = viewModel.state.activeSettings ?? viewModel.state.nextSettings
        HStack(spacing: 9) {
            Menu {
                Button(c.general) { Task { await viewModel.startGeneralChat() } }
                if !viewModel.projects.isEmpty {
                    Divider()
                    ForEach(viewModel.projects, id: \.standardizedFileURL) { project in
                        Button(viewModel.projectDisplayName(project)) {
                            Task { await viewModel.openProject(project) }
                        }
                    }
                }
                Divider()
                Button(c.addProject) { viewModel.beginAddProject() }
            } label: {
                HStack(spacing: 7) {
                    Image(systemName: viewModel.currentWorkspaceIsProject ? "folder.fill" : "bubble.left.and.bubble.right")
                        .font(.system(size: 12))
                    Text(viewModel.headerWorkspaceLabel)
                        .font(.system(size: 12.5, weight: .medium))
                        .lineLimit(1)
                    Image(systemName: "chevron.down")
                        .font(.system(size: 8, weight: .semibold))
                }
                .foregroundStyle(MerlinTheme.text2)
            }
            .menuStyle(.borderlessButton)
            .fixedSize()
            .help(c.text("Switch chat workspace", "채팅 작업 공간 변경"))
            if !showsSessionButton {
                Button {
                    viewModel.showHarnessMap = false
                } label: {
                    Label(c.chat, systemImage: "chevron.left")
                        .font(.system(size: 12.5, weight: .medium))
                }
                .buttonStyle(.plain)
                .foregroundStyle(MerlinTheme.text2)
                .help(c.text("Return to chat", "채팅으로 돌아가기"))
            }
            Spacer(minLength: 12)
            HeaderTag(text: "\(c.modelLabel(active.model)) · \(active.effort)")
            HeaderTag(text: active.routingMode)
            HeaderTag(text: active.autonomyMode)
            if showsSessionButton {
                Button { viewModel.showDrawer.toggle() } label: {
                    Image(systemName: "sidebar.right").font(.system(size: 13)).frame(width: 30, height: 26)
                }
                .buttonStyle(.plain)
                .overlay { RoundedRectangle(cornerRadius: 7).stroke(MerlinTheme.border) }
                .help(c.text("Session details", "세션 상세"))
            }
        }
        .padding(.horizontal, 16)
        .frame(height: 44)
        .background(MerlinTheme.bg)
    }
}

private struct HeaderTag: View {
    let text: String
    var body: some View {
        Text(text)
            .font(.system(size: 11, weight: .medium, design: .monospaced))
            .foregroundStyle(MerlinTheme.text2)
            .padding(.horizontal, 9).padding(.vertical, 3)
            .background(MerlinTheme.elev, in: RoundedRectangle(cornerRadius: 6, style: .continuous))
            .overlay { RoundedRectangle(cornerRadius: 6).stroke(MerlinTheme.border) }
    }
}

private struct EmptyConversation: View {
    @ObservedObject var viewModel: MerlinViewModel

    var body: some View {
        let c = viewModel.copy
        let suggestions = c.language == .korean
            ? ["이 프로젝트를 확인하고 실패한 테스트를 요약해 줘.", "이 작업 공간의 구조를 설명해 줘.", "내가 설명할 문제에 가장 관련된 파일을 찾아 줘."]
            : ["Inspect this project and summarize the failing test.", "Explain the structure of this workspace.", "Find the most relevant file for the issue I describe."]
        VStack(spacing: 0) {
            Text(c.text("Ask about this workspace, or hand off a task.", "이 작업 공간에 관해 묻거나 작업을 맡기세요."))
                .font(.system(size: 16, weight: .medium))
            Text(c.text("Merlin can read and modify files only in the selected local folder.", "멀린은 선택한 로컬 폴더 안의 파일만 읽고 수정할 수 있습니다."))
                .font(.system(size: 13)).foregroundStyle(MerlinTheme.muted).padding(.top, 6)
            VStack(spacing: 8) {
                ForEach(suggestions, id: \.self) { suggestion in
                    Button(suggestion) { Task { await viewModel.sendSuggestion(suggestion) } }
                        .buttonStyle(.plain)
                        .font(.system(size: 13))
                        .foregroundStyle(MerlinTheme.text2)
                        .frame(maxWidth: 460, alignment: .leading)
                        .padding(.horizontal, 15).padding(.vertical, 12)
                        .background(MerlinTheme.elev, in: RoundedRectangle(cornerRadius: 10, style: .continuous))
                        .overlay { RoundedRectangle(cornerRadius: 10).stroke(MerlinTheme.border) }
                }
            }
            .padding(.top, 24)
        }
        .multilineTextAlignment(.center)
    }
}

private struct MessageView: View {
    let message: ChatMessage
    let isPendingApproval: Bool
    @ObservedObject var viewModel: MerlinViewModel

    var body: some View {
        switch message.kind {
        case .user:
            HStack {
                Spacer(minLength: 80)
                Text(message.text).textSelection(.enabled)
                    .font(.system(size: 14)).lineSpacing(3)
                    .padding(.horizontal, 14).padding(.vertical, 10)
                    .background(MerlinTheme.accentWeak, in: RoundedRectangle(cornerRadius: 14, style: .continuous))
                    .overlay { RoundedRectangle(cornerRadius: 14).stroke(MerlinTheme.border) }
            }
        case .assistant:
            VStack(alignment: .leading, spacing: 8) {
                Text("MERLIN").font(.system(size: 11, weight: .medium)).foregroundStyle(MerlinTheme.muted)
                Text(message.text).font(.system(size: 14)).lineSpacing(4).textSelection(.enabled)
            }
            .padding(.vertical, 2)
        case .turnDetails:
            // Ordinary routing and prompt-exposure evidence remains available in
            // the Harness Map. Interrupt the conversation only when the managed
            // harness actually proposes or performs a lifecycle action.
            if let turn = message.turn, !turn.harnessActions.isEmpty {
                TurnDetailsCard(turn: turn, viewModel: viewModel)
            }
        case .approval:
            if let approval = message.approval { ApprovalCard(approval: approval, active: isPendingApproval, viewModel: viewModel) }
        case .commandProposal:
            if let proposal = message.commandProposal { CommandProposalCard(proposal: proposal, viewModel: viewModel) }
        case .note:
            Text(localizedNote(message.text, copy: viewModel.copy))
                .font(.system(size: 12)).foregroundStyle(MerlinTheme.muted)
                .padding(11).background(MerlinTheme.panel, in: RoundedRectangle(cornerRadius: 9, style: .continuous))
                .overlay { RoundedRectangle(cornerRadius: 9).stroke(MerlinTheme.border) }
        }
    }

    private func localizedNote(_ text: String, copy: AppCopy) -> String {
        if text == "Approval denied. The original request was not executed." {
            return copy.text("Approval denied. The original request was not executed.", "승인이 거절되었습니다. 원래 요청은 실행되지 않았습니다.")
        }
        if text.hasPrefix("Feedback recorded as") {
            return copy.text("Feedback recorded. It is health evidence only; it does not trigger an automatic lifecycle change.", "피드백을 기록했습니다. 이는 건강도 증거일 뿐 자동 수명주기 변경을 일으키지 않습니다.")
        }
        return text
    }
}

private struct CommandProposalCard: View {
    let proposal: SlashCommandProposal
    @ObservedObject var viewModel: MerlinViewModel

    var body: some View {
        let c = viewModel.copy
        VStack(alignment: .leading, spacing: 9) {
            HStack(spacing: 7) {
                Image(systemName: "slash.circle")
                    .foregroundStyle(MerlinTheme.muted)
                Text("/\(proposal.command.rawValue)")
                    .font(.system(size: 12, weight: .semibold, design: .monospaced))
                Spacer()
                Text(c.text("NOT EXECUTED", "실행 안 됨"))
                    .font(.system(size: 9.5, weight: .semibold))
                    .foregroundStyle(MerlinTheme.muted)
            }
            Text(proposal.title).font(.system(size: 13, weight: .semibold))
            Text(proposal.detail)
                .font(.system(size: 12))
                .foregroundStyle(MerlinTheme.text2)
                .lineSpacing(3)
            if let suggestedPrompt = proposal.suggestedPrompt {
                Button(c.text("Draft governed request", "관리형 요청 초안 만들기")) {
                    viewModel.draft = suggestedPrompt
                }
                .buttonStyle(ReferenceButtonStyle(tone: .secondary))
            }
        }
        .padding(13)
        .background(MerlinTheme.panel, in: RoundedRectangle(cornerRadius: 10, style: .continuous))
        .overlay { RoundedRectangle(cornerRadius: 10).stroke(MerlinTheme.border) }
    }
}

private struct TurnDetailsCard: View {
    let turn: TurnDetails
    @ObservedObject var viewModel: MerlinViewModel
    @State private var isExpanded = false

    var body: some View {
        let c = viewModel.copy
        VStack(alignment: .leading, spacing: 10) {
            Button {
                withAnimation(.easeOut(duration: 0.14)) { isExpanded.toggle() }
            } label: {
                HStack(spacing: 8) {
                    Image(systemName: "checkmark.circle")
                    Text(c.text("Turn \(turn.turnNumber)", "턴 \(turn.turnNumber)"))
                        .font(.system(size: 12.5, weight: .medium))
                    Text(turn.provisionedSkills.isEmpty
                         ? c.text("No skill", "스킬 없음")
                         : turn.provisionedSkills.map(\.name).joined(separator: ", "))
                        .font(.system(size: 11.5))
                        .foregroundStyle(MerlinTheme.muted)
                        .lineLimit(1)
                    Spacer()
                    Image(systemName: isExpanded ? "chevron.up" : "chevron.down")
                        .font(.system(size: 10, weight: .semibold))
                        .foregroundStyle(MerlinTheme.muted)
                }
                .contentShape(Rectangle())
            }
            .buttonStyle(.plain)
            .accessibilityLabel(c.text("Turn \(turn.turnNumber) details", "턴 \(turn.turnNumber) 상세"))

            if isExpanded {
                if turn.provisionedSkills.isEmpty {
                    Text(c.text("No provisioned skill was returned for this turn.", "이 턴에서 반환된 프로비저닝 스킬이 없습니다."))
                        .font(.system(size: 12)).foregroundStyle(MerlinTheme.muted)
                } else {
                    ForEach(turn.provisionedSkills) { skill in
                        VStack(alignment: .leading, spacing: 2) {
                            Text(skill.name).font(.system(size: 12.5, weight: .medium))
                            Text(skill.why).font(.system(size: 11.5)).foregroundStyle(MerlinTheme.muted).lineLimit(2)
                        }
                    }
                }
                Text("\(turn.routingObservation.source) · " + (turn.routingObservation.finalProvisionedIDs.isEmpty
                     ? c.text("abstained", "기권")
                     : c.text("selected ", "선택 ") + turn.routingObservation.finalProvisionedIDs.joined(separator: ", ")))
                    .font(.system(size: 11.5, design: .monospaced))
                    .foregroundStyle(MerlinTheme.muted)
                Text(c.text("Prompt exposure is not provider-native invocation.", "프롬프트 노출은 공급자 네이티브 호출을 뜻하지 않습니다."))
                    .font(.system(size: 10.5)).foregroundStyle(MerlinTheme.muted)
                if !turn.harnessActions.isEmpty {
                    Divider().overlay(MerlinTheme.border)
                    ForEach(turn.harnessActions) { action in
                        VStack(alignment: .leading, spacing: 2) {
                            Text("\(action.status) · \(action.action ?? action.scope ?? c.text("session action", "세션 작업"))")
                            Text("\(action.riskClass ?? "bounded") · \(action.gateCount ?? 0) gates")
                        }
                        .font(.system(size: 11.5)).foregroundStyle(MerlinTheme.muted)
                    }
                }
                HStack(spacing: 8) {
                    Text(c.text("Was this helpful?", "도움이 되었나요?")).font(.system(size: 12)).foregroundStyle(MerlinTheme.muted)
                    Button(c.text("Pass", "성공")) { Task { await viewModel.recordFeedback("pass") } }
                        .buttonStyle(ReferenceButtonStyle(tone: .secondary)).disabled(viewModel.state.feedbackRecorded)
                    Button(c.text("Fail", "실패")) { Task { await viewModel.recordFeedback("fail") } }
                        .buttonStyle(ReferenceButtonStyle(tone: .secondary)).disabled(viewModel.state.feedbackRecorded)
                }
            }
        }
        .padding(14).background(MerlinTheme.elev, in: RoundedRectangle(cornerRadius: 10, style: .continuous))
        .overlay { RoundedRectangle(cornerRadius: 10).stroke(MerlinTheme.border) }
    }
}

private struct ApprovalCard: View {
    let approval: ApprovalRequest
    let active: Bool
    @ObservedObject var viewModel: MerlinViewModel

    var body: some View {
        let c = viewModel.copy
        VStack(alignment: .leading, spacing: 10) {
            HStack {
                Label(c.text("Approval required", "승인 필요"), systemImage: "hand.raised.fill")
                    .font(.system(size: 13, weight: .semibold)).foregroundStyle(MerlinTheme.accent)
                Spacer()
                Text("strict").font(.system(size: 11, design: .monospaced)).foregroundStyle(MerlinTheme.accent)
            }
            Text(approval.message).font(.system(size: 13)).foregroundStyle(MerlinTheme.text2).lineSpacing(3)
            Text(c.text("capability", "기능") + ": \(approval.capabilityID)")
                .font(.system(size: 12, design: .monospaced)).padding(9)
                .background(MerlinTheme.code, in: RoundedRectangle(cornerRadius: 8, style: .continuous))
            ForEach(approval.plannedMutations, id: \.self) { mutation in
                Text("• \(mutation)").font(.system(size: 12)).foregroundStyle(MerlinTheme.muted)
            }
            if active {
                HStack(spacing: 8) {
                    Button(c.allow) { Task { await viewModel.resolveApproval(approved: true) } }
                        .buttonStyle(ReferenceButtonStyle(tone: .primary))
                    Button(c.deny) { Task { await viewModel.resolveApproval(approved: false) } }
                        .buttonStyle(ReferenceButtonStyle(tone: .secondary))
                }
            }
        }
        .padding(14)
        .background(MerlinTheme.accentWeak, in: RoundedRectangle(cornerRadius: 12, style: .continuous))
        .overlay { RoundedRectangle(cornerRadius: 12).stroke(MerlinTheme.accentEdge) }
    }
}

private struct ComposerView: View {
    @ObservedObject var viewModel: MerlinViewModel
    var composerFocused: FocusState<Bool>.Binding

    var body: some View {
        let c = viewModel.copy
        VStack(spacing: 8) {
            if !viewModel.slashCommandSuggestions.isEmpty {
                SlashCommandPalette(
                    commands: viewModel.slashCommandSuggestions,
                    viewModel: viewModel,
                    focusComposer: { composerFocused.wrappedValue = true }
                )
            }
            HStack(spacing: 8) {
                TextField(c.text("Message Merlin about this workspace…", "이 작업 공간에 대해 멀린에게 메시지 보내기…"), text: $viewModel.draft, axis: .vertical)
                    .textFieldStyle(.plain).lineLimit(1 ... 4)
                    .focused(composerFocused).onSubmit { Task { await viewModel.sendDraft() } }
                    .disabled(!viewModel.composerCanEdit)
                Button { Task { await viewModel.sendDraft() } } label: {
                    HStack(spacing: 5) {
                        Text(viewModel.state.phase == .turnRunning ? c.text("Queue", "대기열") : c.send)
                        Image(systemName: "arrow.up").font(.system(size: 12, weight: .bold))
                    }
                }
                .buttonStyle(ComposerSendButtonStyle())
                .disabled(!viewModel.composerCanSubmit)
            }
            .padding(.horizontal, 12).padding(.vertical, 10)
            .background(MerlinTheme.elev, in: RoundedRectangle(cornerRadius: 14, style: .continuous))
            .overlay { RoundedRectangle(cornerRadius: 14).stroke(MerlinTheme.strongBorder) }
            Text(viewModel.state.phase == .approvalPending
                 ? c.text("Approval is waiting · use /approve or /reject, or the buttons in the card.", "승인 대기 중 · /approve 또는 /reject, 또는 카드의 버튼을 사용하세요.")
                 : viewModel.queuedSteeringPrompt != nil
                 ? c.text("Follow-up queued for the next turn on this thread.", "이 스레드의 다음 턴으로 후속 메시지를 대기열에 넣었습니다.")
                 : viewModel.state.phase == .turnRunning
                 ? c.text("Type a follow-up to queue it. The current CLI turn is not canceled.", "후속 메시지를 입력해 대기열에 넣을 수 있습니다. 현재 CLI 턴은 취소되지 않습니다.")
                 : c.text("Enter to send · Shift+Enter for newline · one blocking turn at a time", "Enter로 보내기 · Shift+Enter로 줄바꿈 · 한 번에 하나의 블로킹 턴"))
                .font(.system(size: 11)).foregroundStyle(MerlinTheme.muted).frame(maxWidth: .infinity)
        }
        .frame(maxWidth: 720)
        .padding(.horizontal, 24).padding(.top, 12).padding(.bottom, 18)
        .frame(maxWidth: .infinity)
        .background(MerlinTheme.bg)
        .overlay(alignment: .top) { Rectangle().fill(MerlinTheme.border).frame(height: 1) }
    }
}

private struct SlashCommandPalette: View {
    let commands: [SlashCommand]
    @ObservedObject var viewModel: MerlinViewModel
    let focusComposer: () -> Void

    var body: some View {
        let c = viewModel.copy
        VStack(alignment: .leading, spacing: 2) {
            ForEach(commands) { command in
                Button {
                    viewModel.selectSlashCommand(command)
                    focusComposer()
                } label: {
                    HStack(spacing: 10) {
                        Text(command.template.trimmingCharacters(in: .whitespaces))
                            .font(.system(size: 11.5, weight: .medium, design: .monospaced))
                            .foregroundStyle(MerlinTheme.text)
                            .frame(width: 118, alignment: .leading)
                        Text(description(for: command, copy: c))
                            .font(.system(size: 11.5))
                            .foregroundStyle(MerlinTheme.muted)
                            .lineLimit(1)
                        Spacer(minLength: 0)
                    }
                    .padding(.horizontal, 11).padding(.vertical, 7)
                    .contentShape(Rectangle())
                }
                .buttonStyle(.plain)
                .accessibilityLabel("/\(command.rawValue)")
            }
        }
        .padding(4)
        .background(MerlinTheme.panel, in: RoundedRectangle(cornerRadius: 10, style: .continuous))
        .overlay { RoundedRectangle(cornerRadius: 10).stroke(MerlinTheme.border) }
        .frame(maxWidth: 720, alignment: .leading)
    }

    private func description(for command: SlashCommand, copy: AppCopy) -> String {
        switch command {
        case .skills:
            copy.text("Browse returned skill contracts", "반환된 스킬 계약 보기")
        case .harness:
            copy.text("Open runtime harness evidence", "런타임 하네스 증거 열기")
        case .createSkill:
            copy.text("Draft a governed creation request", "관리형 생성 요청 초안")
        case .approve:
            copy.text("Allow the waiting approval", "대기 중인 승인 허용")
        case .reject:
            copy.text("Deny the waiting approval", "대기 중인 승인 거절")
        case .skill:
            copy.text("Run an active skill through the bridge", "브리지를 통해 활성 스킬 실행")
        }
    }
}

private struct ComposerSendButtonStyle: ButtonStyle {
    func makeBody(configuration: Configuration) -> some View {
        configuration.label
            .font(.system(size: 13, weight: .semibold))
            .foregroundStyle(MerlinTheme.onAccent)
            .padding(.horizontal, 14).padding(.vertical, 8)
            .background { MerlinPrimaryControlSurface(cornerRadius: 9) }
            .opacity(configuration.isPressed ? 0.78 : 1)
    }
}

private struct HarnessMapScreen: View {
    @ObservedObject var viewModel: MerlinViewModel

    var body: some View {
        VStack(spacing: 0) {
            ChatHeader(viewModel: viewModel, showsSessionButton: false)
            Divider().overlay(MerlinTheme.border)
            HarnessMapView(
                status: viewModel.state.sessionStatus,
                evidence: viewModel.state.harnessMap,
                mode: $viewModel.harnessMapMode
            )
        }
        .background(MerlinTheme.bg)
    }
}

private struct HarnessGraphSurface: View {
    @ObservedObject var viewModel: MerlinViewModel

    var body: some View {
        let c = viewModel.copy
        let map = viewModel.state.harnessMap
        VStack(spacing: 0) {
            HStack {
                VStack(alignment: .leading, spacing: 3) {
                    Text(c.harnessMap).font(.system(size: 14, weight: .medium))
                    Text(c.text("Evidence-only relationship view", "증거 전용 관계 보기"))
                        .font(.system(size: 11)).foregroundStyle(MerlinTheme.muted)
                }
                Spacer()
                Text(map.hasLiveLifecycleEvents ? c.text("lifecycle evidence", "수명주기 증거") : c.text("read-only evidence", "읽기 전용 증거"))
                    .font(.system(size: 11, design: .monospaced)).foregroundStyle(MerlinTheme.muted)
            }
            .padding(.horizontal, 16).frame(height: 52)
            Divider().overlay(MerlinTheme.border)
            GeometryReader { proxy in
                if map.nodes.isEmpty {
                    VStack(spacing: 10) {
                        Image(systemName: "point.3.connected.trianglepath.dotted").font(.system(size: 28)).foregroundStyle(MerlinTheme.muted)
                        Text(c.text("No harness evidence yet", "아직 하네스 증거가 없습니다."))
                            .font(.system(size: 15, weight: .medium))
                        Text(c.text("Completed chat turns add only bridge-returned provisioning, action, and feedback evidence here.", "완료된 채팅 턴이 브리지가 반환한 프로비저닝, 작업, 피드백 증거만 여기에 추가합니다."))
                            .font(.system(size: 12)).foregroundStyle(MerlinTheme.muted).multilineTextAlignment(.center).frame(maxWidth: 360)
                    }
                    .frame(maxWidth: .infinity, maxHeight: .infinity)
                } else {
                    HarnessGraphCanvas(viewModel: viewModel, size: proxy.size)
                }
            }
            .background(
                RadialGradient(colors: [MerlinTheme.accentWeak.opacity(0.55), MerlinTheme.bg], center: .top, startRadius: 0, endRadius: 600)
            )
            HStack(spacing: 5) {
                Circle().fill(MerlinTheme.accent).frame(width: 6, height: 6)
                Text(c.text("Exposure evidence is not provider-native skill invocation.", "노출 증거는 공급자 네이티브 스킬 호출이 아닙니다."))
                    .font(.system(size: 11)).foregroundStyle(MerlinTheme.muted)
            }
            .padding(.horizontal, 14).frame(height: 36).frame(maxWidth: .infinity, alignment: .leading)
            .background(MerlinTheme.panel)
        }
    }
}

private struct HarnessGraphCanvas: View {
    @ObservedObject var viewModel: MerlinViewModel
    let size: CGSize

    var body: some View {
        let nodes = viewModel.state.harnessMap.nodes
        let edges = viewModel.state.harnessMap.edges
        let positions = Dictionary(uniqueKeysWithValues: nodes.enumerated().map { ($0.element.id, position(index: $0.offset, count: nodes.count)) })
        ZStack {
            ForEach(edges) { edge in
                if let source = positions[edge.sourceID], let target = positions[edge.targetID] {
                    Path { path in
                        path.move(to: source)
                        path.addLine(to: target)
                    }
                    .stroke(edgeColor(edge.evidenceKind), style: StrokeStyle(lineWidth: 1.4, dash: edge.evidenceKind == .promptExposure ? [4, 4] : []))
                    .opacity(0.75)
                }
            }
            ForEach(nodes) { node in
                let point = positions[node.id] ?? .zero
                Button { viewModel.selectedHarnessNodeID = node.id } label: {
                    VStack(spacing: 6) {
                        Image(systemName: nodeSymbol(node.kind))
                            .font(.system(size: 13, weight: .medium))
                            .foregroundStyle(nodeColor(node.evidenceKind))
                            .frame(width: 30, height: 30)
                            .background(nodeColor(node.evidenceKind).opacity(0.14), in: Circle())
                            .overlay { Circle().stroke(viewModel.selectedHarnessNodeID == node.id ? nodeColor(node.evidenceKind) : MerlinTheme.border, lineWidth: viewModel.selectedHarnessNodeID == node.id ? 2 : 1) }
                        Text(node.label).font(.system(size: 11, weight: .medium)).foregroundStyle(MerlinTheme.text2).lineLimit(1).frame(maxWidth: 118)
                    }
                }
                .buttonStyle(.plain)
                .position(point)
            }
        }
    }

    private func position(index: Int, count: Int) -> CGPoint {
        let columns = min(3, max(1, count))
        let rows = Int(ceil(Double(count) / Double(columns)))
        let column = index % columns
        let row = index / columns
        let horizontalInset: CGFloat = 80
        let verticalInset: CGFloat = 72
        let x = count == 1 ? size.width / 2 : horizontalInset + (size.width - horizontalInset * 2) * CGFloat(column) / CGFloat(max(columns - 1, 1))
        let y = rows == 1 ? size.height / 2 : verticalInset + (size.height - verticalInset * 2) * CGFloat(row) / CGFloat(max(rows - 1, 1))
        return CGPoint(x: x, y: y)
    }

    private func nodeColor(_ kind: HarnessEvidenceKind) -> Color {
        switch kind {
        case .promptExposure: MerlinTheme.accent
        case .selectionObservation: MerlinTheme.text2
        case .feedbackOutcome: MerlinTheme.green
        case .copyOnWriteAction: MerlinTheme.green
        case .lifecycleEvent: MerlinTheme.red
        }
    }

    private func edgeColor(_ kind: HarnessEvidenceKind) -> Color { nodeColor(kind) }

    private func nodeSymbol(_ kind: HarnessNodeKind) -> String {
        switch kind {
        case .turn: "bubble.left"
        case .skill: "square.stack.3d.up"
        case .action: "checkmark.shield"
        }
    }
}

private struct HarnessGraphInspector: View {
    @ObservedObject var viewModel: MerlinViewModel

    var body: some View {
        let c = viewModel.copy
        let map = viewModel.state.harnessMap
        let selected = map.nodes.first { $0.id == viewModel.selectedHarnessNodeID }
        ScrollView {
            VStack(alignment: .leading, spacing: 18) {
                Text(c.text("EVIDENCE INSPECTOR", "증거 검사기"))
                    .font(.system(size: 11, weight: .medium)).foregroundStyle(MerlinTheme.muted)
                if let selected {
                    InspectorBlock(title: c.text("Selected node", "선택한 노드")) {
                        Text(selected.label).font(.system(size: 13, weight: .medium))
                        InspectorLine(label: c.text("Type", "유형"), value: selected.kind.rawValue)
                        InspectorLine(label: c.text("Evidence", "증거"), value: selected.evidenceKind.rawValue)
                        if let trace = selected.tracePointer {
                            Text(trace).font(.system(size: 11, design: .monospaced)).foregroundStyle(MerlinTheme.muted).textSelection(.enabled)
                        }
                    }
                    InspectorBlock(title: c.text("Observed relations", "관찰된 관계")) {
                        let relations = map.edges.filter { $0.sourceID == selected.id || $0.targetID == selected.id }
                        if relations.isEmpty {
                            Text(c.text("No returned relation for this node.", "이 노드에 반환된 관계가 없습니다.")).font(.system(size: 11.5)).foregroundStyle(MerlinTheme.muted)
                        } else {
                            ForEach(relations) { relation in
                                Text("\(relation.kind.rawValue) · \(relation.evidenceKind.rawValue)")
                                    .font(.system(size: 11.5)).foregroundStyle(MerlinTheme.text2)
                            }
                        }
                    }
                } else if map.events.isEmpty {
                    VStack(alignment: .leading, spacing: 7) {
                        Text(c.text("No returned evidence", "반환된 증거 없음"))
                            .font(.system(size: 13, weight: .medium))
                        Text(c.text("This inspector stays read-only until a bridge response returns evidence.", "브리지 응답이 증거를 반환할 때까지 이 검사기는 읽기 전용입니다."))
                            .font(.system(size: 11.5)).foregroundStyle(MerlinTheme.muted).lineSpacing(2)
                    }
                } else {
                    InspectorBlock(title: c.text("Evidence feed", "증거 피드")) {
                        ForEach(map.events.reversed()) { event in
                            VStack(alignment: .leading, spacing: 3) {
                                Text(event.kind.rawValue.uppercased()).font(.system(size: 10, weight: .medium)).foregroundStyle(MerlinTheme.accent)
                                Text(event.title).font(.system(size: 12, weight: .medium))
                                Text(event.detail).font(.system(size: 11)).foregroundStyle(MerlinTheme.muted).lineLimit(3)
                            }
                            if event.id != map.events.last?.id { Divider().overlay(MerlinTheme.border) }
                        }
                    }
                }
                InspectorBlock(title: c.text("Boundary", "경계")) {
                    Text(c.text("Provisioning is recorded as exposure evidence. This UI does not claim provider-native invocation or lifecycle completion.", "프로비저닝은 노출 증거로 기록됩니다. 이 UI는 공급자 네이티브 호출이나 수명주기 완료를 주장하지 않습니다."))
                        .font(.system(size: 11)).foregroundStyle(MerlinTheme.muted).lineSpacing(2)
                }
            }
            .padding(16)
        }
        .background(MerlinTheme.panel)
    }
}

private struct InspectorBlock<Content: View>: View {
    let title: String
    @ViewBuilder let content: Content
    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            Text(title.uppercased()).font(.system(size: 10.5)).foregroundStyle(MerlinTheme.muted)
            content
        }
    }
}

private struct InspectorLine: View {
    let label: String
    let value: String
    var body: some View {
        HStack(alignment: .firstTextBaseline) {
            Text(label).foregroundStyle(MerlinTheme.muted)
            Spacer(minLength: 8)
            Text(value).multilineTextAlignment(.trailing)
        }
        .font(.system(size: 11.5))
    }
}

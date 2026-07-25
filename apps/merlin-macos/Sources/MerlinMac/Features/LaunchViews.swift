import SwiftUI

struct AccountView: View {
    @ObservedObject var viewModel: MerlinViewModel

    var body: some View {
        let c = viewModel.copy
        GeometryReader { proxy in
            let contentWidth = min(max(proxy.size.width - 80, 0), 420)
            VStack(spacing: 0) {
                Spacer(minLength: 36)
                VStack(spacing: 0) {
                BrandMark(size: 64)
                    .padding(.bottom, 20)
                Text(c.text("Connect your coding agent", "코딩 에이전트 연결"))
                    .font(.system(size: 22, weight: .semibold))
                    .tracking(-0.2)
                switch viewModel.state.phase {
                case .checking:
                    HStack(spacing: 10) {
                        ProgressView().controlSize(.small)
                        Text(c.text("Checking Codex CLI…", "Codex CLI 확인 중…"))
                    }
                    .font(.system(size: 14)).foregroundStyle(MerlinTheme.muted)
                    .padding(.top, 22)
                case .loggedOut:
                    let account = viewModel.state.account
                    let cliUnavailable = account?.state == "cli_missing" || account?.state == "check_failed"
                    Text(cliUnavailable
                         ? c.text("Choose the Codex CLI executable to continue.", "계속하려면 Codex CLI 실행 파일을 선택하세요.")
                         : c.text("You're signed out. Choose how to connect.", "로그아웃되어 있습니다. 연결 방식을 선택하세요."))
                        .font(.system(size: 14)).foregroundStyle(MerlinTheme.text2)
                        .padding(.top, 8)

                    HStack(spacing: 2) {
                        AccountMethodTab(title: c.text("ChatGPT account", "ChatGPT 계정"), selected: true)
                        AccountMethodTab(title: c.text("API key", "API 키"), selected: false)
                            .disabled(true)
                            .help(c.text("Not available in this build", "이 빌드에서는 사용할 수 없음"))
                    }
                    .padding(2)
                    .background(MerlinTheme.panel, in: RoundedRectangle(cornerRadius: 9, style: .continuous))
                    .overlay { RoundedRectangle(cornerRadius: 9, style: .continuous).stroke(MerlinTheme.border) }
                    .padding(.top, 18)

                    if cliUnavailable {
                        VStack(alignment: .leading, spacing: 8) {
                            Text(c.text("Codex CLI auto-detect did not find a usable authenticated CLI.", "Codex CLI 자동 감지가 사용할 수 있는 인증 CLI를 찾지 못했습니다."))
                                .font(.system(size: 12.5)).foregroundStyle(MerlinTheme.text2).lineSpacing(2)
                            Button(c.text("Choose Codex CLI path", "Codex CLI 경로 선택")) {
                                viewModel.chooseCodexCLIExecutable()
                            }
                            .buttonStyle(ReferenceButtonStyle(tone: .secondary))
                        }
                        .padding(14)
                        .frame(maxWidth: .infinity, alignment: .leading)
                        .background(MerlinTheme.elev, in: RoundedRectangle(cornerRadius: 10, style: .continuous))
                        .overlay { RoundedRectangle(cornerRadius: 10, style: .continuous).stroke(MerlinTheme.border) }
                        .padding(.top, 14)
                    }

                    if !cliUnavailable {
                        VStack(alignment: .leading, spacing: 0) {
                            Text(c.text("Sign in with your ChatGPT account", "ChatGPT 계정으로 로그인"))
                                .font(.system(size: 14, weight: .semibold))
                            Text(c.text("Opens device authorization in the Codex CLI. Login is handled by the CLI — the app never sees a token.", "Codex CLI에서 기기 인증을 엽니다. 로그인은 CLI가 처리하며 앱은 토큰을 보지 않습니다."))
                                .font(.system(size: 13)).foregroundStyle(MerlinTheme.text2).lineSpacing(3)
                                .padding(.top, 6)
                            Button(c.text("Connect with ChatGPT", "ChatGPT로 연결")) {
                                Task { await viewModel.connectAccount() }
                            }
                            .buttonStyle(AccountConnectButtonStyle())
                            .padding(.top, 14)
                            if let notice = viewModel.connectionNoticeText {
                                VStack(alignment: .leading, spacing: 10) {
                                    Text(notice)
                                        .font(.system(size: 11)).foregroundStyle(MerlinTheme.muted).lineSpacing(2)
                                    Button(c.text("Refresh status", "상태 새로고침")) {
                                        Task { await viewModel.refreshAfterConnection() }
                                    }
                                    .buttonStyle(ReferenceButtonStyle(tone: .secondary))
                                }
                                .padding(.top, 12)
                            }
                        }
                        .padding(16)
                        .frame(maxWidth: .infinity, alignment: .leading)
                        .background(MerlinTheme.elev, in: RoundedRectangle(cornerRadius: 10, style: .continuous))
                        .overlay { RoundedRectangle(cornerRadius: 10, style: .continuous).stroke(MerlinTheme.border) }
                        .padding(.top, 16)
                    }

                    Text(cliDetectionText(account, copy: c))
                        .font(.system(size: 12)).foregroundStyle(MerlinTheme.muted)
                        .padding(.top, 12)
                case .connected:
                    if let account = viewModel.state.account {
                        VStack(alignment: .leading, spacing: 11) {
                            StatusLine(label: c.text("Auth method", "인증 방식"), value: account.authMethod ?? "—")
                            StatusLine(label: c.text("CLI version", "CLI 버전"), value: account.cliVersion ?? "—")
                            HStack {
                                Text("Codex CLI").foregroundStyle(MerlinTheme.muted)
                                Spacer()
                                HStack(spacing: 6) {
                                    Circle().fill(MerlinTheme.green).frame(width: 7, height: 7)
                                    Text(c.codexCLIConnected).foregroundStyle(MerlinTheme.green)
                                }
                            }
                            .font(.system(size: 13))
                        }
                        .padding(16).frame(maxWidth: .infinity).merlinPanel(cornerRadius: 10)
                        .padding(.top, 22)
                    }
                    if !viewModel.isUIPreview {
                        Text(c.providerNotVerifiedYet)
                            .font(.system(size: 12)).foregroundStyle(MerlinTheme.muted)
                            .padding(.top, 10)
                    }
                    Button(c.text("Continue", "계속")) { Task { await viewModel.startGeneralChat() } }
                        .buttonStyle(AccountContinueButtonStyle())
                        .padding(.top, 16)
                default:
                    EmptyView()
                }
            }
                .frame(width: contentWidth)
                .multilineTextAlignment(.center)
                Spacer(minLength: 36)
            }
            .frame(maxWidth: .infinity, maxHeight: .infinity)
        }
        .background(MerlinTheme.bg)
    }

    private func cliDetectionText(_ account: AccountStatus?, copy: AppCopy) -> String {
        guard let account else { return copy.text("Codex CLI not detected", "Codex CLI 감지되지 않음") }
        guard let version = account.cliVersion else {
            return copy.text("Codex CLI not detected", "Codex CLI 감지되지 않음")
        }
        if account.connected {
            return copy.text("Codex CLI authenticated · \(version)", "Codex CLI 인증됨 · \(version)")
        }
        return copy.text("Codex CLI detected · \(version)", "Codex CLI 감지됨 · \(version)")
    }
}

/// Account recovery surface opened from an active chat. Its actions deliberately
/// reuse the launch flow while asking the view model to preserve the provider thread.
struct AccountConnectionSheet: View {
    @ObservedObject var viewModel: MerlinViewModel
    @Environment(\.dismiss) private var dismiss

    private var account: AccountStatus? { viewModel.state.account }
    private var cliUnavailable: Bool {
        account?.state == "cli_missing" || account?.state == "check_failed"
    }

    var body: some View {
        let c = viewModel.copy
        VStack(alignment: .leading, spacing: 18) {
            HStack(alignment: .top, spacing: 12) {
                BrandMark(size: 28)
                VStack(alignment: .leading, spacing: 3) {
                    Text(c.text("Codex connection", "Codex 연결"))
                        .font(.system(size: 18, weight: .semibold))
                    Text(c.text(
                        "Check or recover your account without leaving this chat.",
                        "현재 채팅을 벗어나지 않고 계정을 확인하거나 복구합니다."
                    ))
                    .font(.system(size: 12.5))
                    .foregroundStyle(MerlinTheme.text2)
                }
                Spacer(minLength: 8)
                Button { dismiss() } label: {
                    Image(systemName: "xmark")
                        .font(.system(size: 11, weight: .semibold))
                        .frame(width: 26, height: 26)
                }
                .buttonStyle(.plain)
                .foregroundStyle(MerlinTheme.muted)
                .help(c.text("Close", "닫기"))
            }

            VStack(alignment: .leading, spacing: 11) {
                HStack(spacing: 7) {
                    Circle()
                        .fill(account?.connected == true ? MerlinTheme.green : MerlinTheme.muted)
                        .frame(width: 8, height: 8)
                    Text(account?.connected == true
                         ? c.text("Codex connected", "Codex 연결됨")
                         : c.text("Codex disconnected", "Codex 연결 안 됨"))
                        .font(.system(size: 14, weight: .semibold))
                }
                if let account {
                    AccountSheetStatusLine(
                        label: c.text("Auth method", "인증 방식"),
                        value: account.authMethod ?? "—"
                    )
                    AccountSheetStatusLine(
                        label: c.text("CLI version", "CLI 버전"),
                        value: account.cliVersion ?? "—"
                    )
                } else {
                    Text(c.text("Status has not been checked yet.", "아직 상태를 확인하지 않았습니다."))
                        .font(.system(size: 12.5))
                        .foregroundStyle(MerlinTheme.muted)
                }
            }
            .padding(14)
            .frame(maxWidth: .infinity, alignment: .leading)
            .background(MerlinTheme.elev, in: RoundedRectangle(cornerRadius: 10, style: .continuous))
            .overlay { RoundedRectangle(cornerRadius: 10, style: .continuous).stroke(MerlinTheme.border) }

            HStack(spacing: 9) {
                Button(c.text("Refresh status", "상태 새로고침")) {
                    Task { await viewModel.refreshAccountStatusPreservingSession() }
                }
                .buttonStyle(ReferenceButtonStyle(tone: .secondary))

                Button(c.text("Choose Codex CLI path", "Codex CLI 경로 선택")) {
                    viewModel.chooseCodexCLIExecutable(preservingCurrentSession: true)
                }
                .buttonStyle(ReferenceButtonStyle(tone: .secondary))
            }

            if account?.connected != true {
                VStack(alignment: .leading, spacing: 9) {
                    if cliUnavailable {
                        Text(c.text(
                            "A usable Codex CLI was not found. Choose its executable, then refresh status.",
                            "사용할 수 있는 Codex CLI를 찾지 못했습니다. 실행 파일을 선택한 뒤 상태를 새로고침하세요."
                        ))
                        .font(.system(size: 12.5))
                        .foregroundStyle(MerlinTheme.text2)
                    }
                    Button(c.text("Connect with ChatGPT", "ChatGPT로 연결")) {
                        Task { await viewModel.connectAccount(preservingCurrentSession: true) }
                    }
                    .buttonStyle(AccountConnectButtonStyle())
                }
                .padding(14)
                .frame(maxWidth: .infinity, alignment: .leading)
                .background(MerlinTheme.elev, in: RoundedRectangle(cornerRadius: 10, style: .continuous))
                .overlay { RoundedRectangle(cornerRadius: 10, style: .continuous).stroke(MerlinTheme.border) }
            }

            if let notice = viewModel.connectionNoticeText {
                Text(notice)
                    .font(.system(size: 11.5))
                    .foregroundStyle(MerlinTheme.muted)
                    .lineSpacing(2)
            }
            if let error = viewModel.accountConnectionError {
                Text(error)
                    .font(.system(size: 11.5))
                    .foregroundStyle(MerlinTheme.red)
                    .lineSpacing(2)
            }

            Text(c.text(
                "Device authorization runs in Terminal. This app never reads or stores device codes, tokens, or Terminal output.",
                "기기 인증은 터미널에서 실행됩니다. 이 앱은 기기 코드·토큰·터미널 출력을 읽거나 저장하지 않습니다."
            ))
            .font(.system(size: 11.5))
            .foregroundStyle(MerlinTheme.muted)
            .lineSpacing(2)

            HStack {
                Spacer()
                Button(c.text("Done", "완료")) { dismiss() }
                    .buttonStyle(ReferenceButtonStyle(tone: .secondary))
            }
        }
        .padding(22)
        .background(MerlinTheme.bg)
    }
}

private struct AccountSheetStatusLine: View {
    let label: String
    let value: String

    var body: some View {
        HStack(alignment: .firstTextBaseline) {
            Text(label).foregroundStyle(MerlinTheme.muted)
            Spacer()
            Text(value).fontWeight(.medium).textSelection(.enabled)
        }
        .font(.system(size: 12.5))
    }
}

private struct AccountMethodTab: View {
    let title: String
    let selected: Bool

    var body: some View {
        Text(title)
            .font(.system(size: 13, weight: selected ? .semibold : .regular))
            .foregroundStyle(selected ? MerlinTheme.text : MerlinTheme.muted)
            .frame(maxWidth: .infinity)
            .padding(.vertical, 8)
            .background {
                if selected { MerlinSelectionSurface(cornerRadius: 7) }
            }
    }
}

private struct AccountConnectButtonStyle: ButtonStyle {
    func makeBody(configuration: Configuration) -> some View {
        configuration.label
            .font(.system(size: 14, weight: .semibold))
            .foregroundStyle(MerlinTheme.onAccent)
            .frame(maxWidth: .infinity)
            .padding(.vertical, 10)
            .background { MerlinPrimaryControlSurface(cornerRadius: 8) }
            .opacity(configuration.isPressed ? 0.78 : 1)
    }
}

private struct AccountContinueButtonStyle: ButtonStyle {
    func makeBody(configuration: Configuration) -> some View {
        configuration.label
            .font(.system(size: 14, weight: .medium))
            .foregroundStyle(MerlinTheme.onAccent)
            .frame(maxWidth: .infinity)
            .padding(.vertical, 11)
            .background { MerlinPrimaryControlSurface(cornerRadius: 9) }
            .opacity(configuration.isPressed ? 0.78 : 1)
    }
}

struct WorkspaceSelectionView: View {
    @ObservedObject var viewModel: MerlinViewModel

    var body: some View {
        let c = viewModel.copy
        GeometryReader { proxy in
            let contentWidth = min(max(proxy.size.width - 80, 0), 540)
            VStack(spacing: 0) {
                Spacer(minLength: 36)
            VStack(alignment: .leading, spacing: 0) {
                VStack(alignment: .leading, spacing: 6) {
                    Text(c.text("Choose a workspace", "작업 공간 선택"))
                        .font(.system(size: 20, weight: .semibold))
                        .tracking(-0.2)
                    Text(c.text("This is a coding agent — pick the local folder it can read and modify.", "코딩 에이전트가 읽고 수정할 수 있는 로컬 폴더를 선택하세요."))
                        .font(.system(size: 14)).foregroundStyle(MerlinTheme.text2).lineSpacing(3)
                }

                Button { viewModel.showWorkspacePathPicker = true } label: {
                    HStack(spacing: 12) {
                        Image(systemName: "folder")
                            .font(.system(size: 17)).foregroundStyle(MerlinTheme.text2)
                        if let workspace = viewModel.state.workspace {
                            VStack(alignment: .leading, spacing: 3) {
                                Text(workspace.lastPathComponent).font(.system(size: 14, weight: .medium)).foregroundStyle(MerlinTheme.text)
                                Text(workspace.path).font(.system(size: 12, design: .monospaced)).foregroundStyle(MerlinTheme.muted).lineLimit(1)
                            }
                        } else {
                            Text(c.text("Choose workspace path", "작업 경로 선택"))
                                .font(.system(size: 14)).foregroundStyle(MerlinTheme.text)
                        }
                        Spacer()
                        Text(viewModel.state.workspace == nil ? c.text("Choose", "선택") : c.text("Change", "변경"))
                            .font(.system(size: 12)).foregroundStyle(MerlinTheme.accent)
                        Image(systemName: "chevron.right").font(.system(size: 10)).foregroundStyle(MerlinTheme.muted)
                    }
                    .padding(.horizontal, 15).padding(.vertical, 13)
                    .contentShape(Rectangle())
                }
                .buttonStyle(.plain)
                .background(MerlinTheme.elev, in: RoundedRectangle(cornerRadius: 12, style: .continuous))
                .overlay { RoundedRectangle(cornerRadius: 12, style: .continuous).stroke(MerlinTheme.border) }
                .padding(.top, 22)

                if let issue = viewModel.workspaceIssue {
                    Text(workspaceIssueText(issue, copy: c))
                        .font(.system(size: 12)).foregroundStyle(MerlinTheme.red)
                        .padding(.top, 14)
                }

                Button {
                    Task { await viewModel.startSession() }
                } label: {
                    HStack(spacing: 8) {
                        if viewModel.state.phase == .sessionStarting { ProgressView().controlSize(.small) }
                        Text(viewModel.state.phase == .sessionStarting ? c.text("Starting session…", "세션 시작 중…") : c.text("Start", "시작"))
                    }
                    .frame(maxWidth: .infinity)
                }
                .buttonStyle(WorkspaceStartButtonStyle())
                .disabled(viewModel.state.workspace == nil || viewModel.state.phase == .sessionStarting)
                .padding(.top, 24)
            }
            .frame(width: contentWidth, alignment: .leading)
            .offset(y: -48)
            Spacer(minLength: 36)
            }
            .frame(maxWidth: .infinity, maxHeight: .infinity)
        }
        .background(MerlinTheme.bg)
    }

    private func workspaceIssueText(_ issue: WorkspaceDirectoryError, copy: AppCopy) -> String {
        switch issue {
        case .invalidName:
            copy.text("Enter a valid folder name. Names cannot be empty, '.', '..', or include /, \\, or :.", "유효한 폴더 이름을 입력하세요. 비어 있거나 '.', '..', /, \\, :를 포함한 이름은 사용할 수 없습니다.")
        case .parentUnavailable:
            copy.text("The selected parent folder is no longer available.", "선택한 상위 폴더를 더 이상 사용할 수 없습니다.")
        case .alreadyExists:
            copy.text("A folder with that name already exists. Existing folders are never overwritten.", "같은 이름의 폴더가 이미 있습니다. 기존 폴더는 절대 덮어쓰지 않습니다.")
        case .createFailed:
            copy.text("The new folder could not be created safely. Check the location and permissions.", "새 폴더를 안전하게 만들지 못했습니다. 위치와 권한을 확인하세요.")
        }
    }
}

private struct WorkspaceStartButtonStyle: ButtonStyle {
    @Environment(\.isEnabled) private var isEnabled

    func makeBody(configuration: Configuration) -> some View {
        configuration.label
            .font(.system(size: 14, weight: .semibold))
            .foregroundStyle(isEnabled ? MerlinTheme.onAccent : MerlinTheme.muted)
            .frame(maxWidth: .infinity)
            .padding(.vertical, 12)
            .background {
                if isEnabled {
                    MerlinPrimaryControlSurface(cornerRadius: 9)
                } else {
                    let shape = RoundedRectangle(cornerRadius: 9, style: .continuous)
                    shape.fill(MerlinTheme.elev)
                        .overlay { shape.stroke(MerlinTheme.border, lineWidth: 1) }
                }
            }
            .opacity(configuration.isPressed && isEnabled ? 0.78 : 1)
    }
}

struct SafeErrorView: View {
    @ObservedObject var viewModel: MerlinViewModel

    var body: some View {
        let c = viewModel.copy
        VStack {
            Spacer()
            VStack(alignment: .leading, spacing: 13) {
                Label(c.text("Safe stop", "안전 중지"), systemImage: "exclamationmark.triangle.fill")
                    .font(.system(size: 15, weight: .semibold)).foregroundStyle(MerlinTheme.red)
                Text(c.text("The app stopped this request safely. The original bridge detail is preserved below.", "앱이 이 요청을 안전하게 중지했습니다. 원래 브리지 상세 정보는 아래에 보존됩니다."))
                    .font(.system(size: 13)).foregroundStyle(MerlinTheme.text2)
                Text(viewModel.state.safeErrorMessage ?? c.text("The local bridge stopped safely.", "로컬 브리지가 안전하게 중지되었습니다."))
                    .font(.system(size: 12, design: .monospaced)).foregroundStyle(MerlinTheme.muted).textSelection(.enabled)
                HStack {
                    Button(c.text("Try account check", "계정 다시 확인")) { Task { await viewModel.bootstrap() } }
                        .buttonStyle(ReferenceButtonStyle(tone: .primary))
                    Button(c.text("Dismiss", "닫기")) { viewModel.dismissError() }
                        .buttonStyle(ReferenceButtonStyle(tone: .secondary))
                }
            }
            .padding(20).frame(maxWidth: 480).merlinPanel(cornerRadius: 14)
            Spacer()
        }
        .background(MerlinTheme.bg)
    }
}

private struct StatusLine: View {
    let label: String
    let value: String

    var body: some View {
        HStack(alignment: .firstTextBaseline) {
            Text(label).foregroundStyle(MerlinTheme.muted)
            Spacer()
            Text(value).fontWeight(.medium).textSelection(.enabled)
        }
        .font(.system(size: 12))
    }
}

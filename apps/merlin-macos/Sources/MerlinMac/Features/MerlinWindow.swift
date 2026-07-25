import SwiftUI

struct MerlinWindow: View {
    @ObservedObject var viewModel: MerlinViewModel

    var body: some View {
        ZStack {
            VStack(spacing: 0) {
                ReferenceTitleBar(viewModel: viewModel)
                Group {
                    switch viewModel.state.phase {
                    case .checking, .loggedOut, .connected:
                        AccountView(viewModel: viewModel)
                    case .workspaceSelection, .sessionStarting:
                        SessionStartingView(viewModel: viewModel)
                    case .chatReady, .turnRunning, .approvalPending:
                        ChatShellView(viewModel: viewModel)
                    case .safeError:
                        SafeErrorView(viewModel: viewModel)
                    }
                }
                .frame(maxWidth: .infinity, maxHeight: .infinity)
            }
            if viewModel.showLanguagePicker {
                LanguageOverlay(viewModel: viewModel)
            }
            if viewModel.showWorkspacePathPicker {
                WorkspacePathOverlay(viewModel: viewModel)
            }
        }
        .frame(minWidth: 920, minHeight: 620)
        .background(MerlinTheme.desk)
        .preferredColorScheme(viewModel.preferredColorScheme)
        .sheet(isPresented: $viewModel.showAccountConnectionSheet) {
            AccountConnectionSheet(viewModel: viewModel)
                .frame(minWidth: 440, minHeight: 430)
        }
    }
}

private struct SessionStartingView: View {
    @ObservedObject var viewModel: MerlinViewModel

    var body: some View {
        VStack(spacing: 12) {
            ProgressView().controlSize(.regular)
            Text(viewModel.copy.text("Starting chat…", "채팅 시작 중…"))
                .font(.system(size: 14)).foregroundStyle(MerlinTheme.text2)
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity)
        .background(MerlinTheme.bg)
    }
}

private struct ReferenceTitleBar: View {
    @ObservedObject var viewModel: MerlinViewModel

    var body: some View {
        let c = viewModel.copy
        ZStack {
            HStack(spacing: 5) {
                Spacer()
                Button { viewModel.showLanguagePicker = true } label: {
                    Image(systemName: "globe")
                        .font(.system(size: 12, weight: .medium))
                        .frame(width: 30, height: 26)
                }
                .buttonStyle(.plain)
                .overlay { RoundedRectangle(cornerRadius: 7).stroke(MerlinTheme.border) }
                .help(c.interfaceLanguage)
                Button { viewModel.toggleAppearance() } label: {
                    Image(systemName: viewModel.preferredColorScheme == .dark ? "sun.max" : "moon")
                        .font(.system(size: 12, weight: .medium))
                        .frame(width: 30, height: 26)
                        .overlay { RoundedRectangle(cornerRadius: 7).stroke(MerlinTheme.border) }
                }
                .buttonStyle(.plain)
                .help(viewModel.preferredColorScheme == .dark ? c.lightAppearance : c.darkAppearance)
            }
            .padding(.trailing, 14)
            HStack(spacing: 7) {
                BrandMark(size: 18)
                Text("Merlin")
                    .font(.system(size: 13, weight: .medium))
                    .foregroundStyle(MerlinTheme.text2)
                if viewModel.isUIPreview {
                    Text("UI Preview")
                        .font(.system(size: 10, weight: .medium, design: .monospaced))
                        .foregroundStyle(MerlinTheme.muted)
                        .padding(.horizontal, 6).padding(.vertical, 2)
                        .background(MerlinTheme.elev, in: Capsule())
                        .overlay { Capsule().stroke(MerlinTheme.border) }
                }
            }
        }
        .frame(maxWidth: .infinity)
        .frame(height: 40)
        .background(MerlinTheme.panel)
        .overlay(alignment: .bottom) { Rectangle().fill(MerlinTheme.border).frame(height: 1) }
    }
}

private struct LanguageOverlay: View {
    @ObservedObject var viewModel: MerlinViewModel

    var body: some View {
        ZStack {
            Color.black.opacity(0.22)
                .ignoresSafeArea()
                .contentShape(Rectangle())
                .onTapGesture { viewModel.showLanguagePicker = false }
            VStack(alignment: .leading, spacing: 14) {
                HStack {
                    Text("언어 설정 / Language")
                        .font(.system(size: 15, weight: .semibold))
                    Spacer()
                    Button { viewModel.showLanguagePicker = false } label: {
                        Image(systemName: "xmark").font(.system(size: 11, weight: .semibold)).frame(width: 24, height: 24)
                    }
                    .buttonStyle(.plain)
                    .foregroundStyle(MerlinTheme.muted)
                }
                VStack(spacing: 4) {
                    ForEach(AppLanguage.allCases) { language in
                        Button {
                            viewModel.setLanguage(language)
                            viewModel.showLanguagePicker = false
                        } label: {
                            HStack {
                                Text(language.displayName).font(.system(size: 13, weight: .medium))
                                Spacer()
                                if viewModel.language == language {
                                    Image(systemName: "checkmark").font(.system(size: 12, weight: .semibold)).foregroundStyle(MerlinTheme.accent)
                                }
                            }
                            .padding(.horizontal, 11).frame(height: 36)
                            .background {
                                if viewModel.language == language { MerlinSelectionSurface(cornerRadius: 7) }
                            }
                        }
                        .buttonStyle(.plain)
                    }
                }
            }
            .padding(16)
            .frame(width: 292)
            .background(MerlinTheme.elev, in: RoundedRectangle(cornerRadius: 12, style: .continuous))
            .overlay { RoundedRectangle(cornerRadius: 12, style: .continuous).stroke(MerlinTheme.border) }
            .shadow(color: Color.black.opacity(0.28), radius: 24, y: 12)
        }
        .transition(.opacity)
    }
}

private struct WorkspacePathOverlay: View {
    @ObservedObject var viewModel: MerlinViewModel

    var body: some View {
        ZStack {
            Color.black.opacity(0.22)
                .ignoresSafeArea()
                .contentShape(Rectangle())
                .onTapGesture { viewModel.showWorkspacePathPicker = false }
            VStack(alignment: .leading, spacing: 14) {
                HStack {
                    Text(viewModel.isAddingProject ? "Add project / 프로젝트 추가" : "Choose workspace path / 작업 경로 선택")
                        .font(.system(size: 15, weight: .semibold))
                    Spacer()
                    Button { viewModel.showWorkspacePathPicker = false } label: {
                        Image(systemName: "xmark").font(.system(size: 11, weight: .semibold)).frame(width: 24, height: 24)
                    }
                    .buttonStyle(.plain)
                    .foregroundStyle(MerlinTheme.muted)
                }
                VStack(spacing: 4) {
                    WorkspacePathChoice(title: "Use existing folder / 기존 폴더 사용", symbol: "folder") {
                        if viewModel.chooseWorkspace() { viewModel.showWorkspacePathPicker = false }
                    }
                    WorkspacePathChoice(title: "Create new folder / 새 폴더 생성", symbol: "folder.badge.plus") {
                        if viewModel.createNewWorkspace() { viewModel.showWorkspacePathPicker = false }
                    }
                }
            }
            .padding(16)
            .frame(width: 350)
            .background(MerlinTheme.elev, in: RoundedRectangle(cornerRadius: 12, style: .continuous))
            .overlay { RoundedRectangle(cornerRadius: 12, style: .continuous).stroke(MerlinTheme.border) }
            .shadow(color: Color.black.opacity(0.28), radius: 24, y: 12)
        }
        .transition(.opacity)
    }
}

private struct WorkspacePathChoice: View {
    let title: String
    let symbol: String
    let action: () -> Void

    var body: some View {
        Button(action: action) {
            HStack(spacing: 11) {
                Image(systemName: symbol).font(.system(size: 15)).foregroundStyle(MerlinTheme.text2)
                Text(title).font(.system(size: 13, weight: .medium)).foregroundStyle(MerlinTheme.text)
                Spacer()
                Image(systemName: "chevron.right").font(.system(size: 10)).foregroundStyle(MerlinTheme.muted)
            }
            .padding(.horizontal, 11).frame(height: 42)
            .background(MerlinTheme.panel, in: RoundedRectangle(cornerRadius: 8, style: .continuous))
            .overlay { RoundedRectangle(cornerRadius: 8, style: .continuous).stroke(MerlinTheme.border) }
        }
        .buttonStyle(.plain)
    }
}

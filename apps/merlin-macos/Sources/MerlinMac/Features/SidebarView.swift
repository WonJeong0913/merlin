import SwiftUI

struct SidebarView: View {
    @ObservedObject var viewModel: MerlinViewModel
    let compact: Bool

    var body: some View {
        let c = viewModel.copy
        let generalChats = viewModel.recentSessionHistory.filter { !$0.isProject }
        VStack(alignment: compact ? .center : .leading, spacing: 0) {
            SidebarNavigationButton(
                title: c.harnessMap,
                symbol: "point.3.connected.trianglepath.dotted",
                compact: compact,
                active: viewModel.showHarnessMap
            ) {
                viewModel.harnessMapMode = .graph
                viewModel.showHarnessMap = true
            }
            .padding(.horizontal, compact ? 10 : 8)
            .padding(.top, 14)

            if !compact {
                SidebarSectionHeader(title: c.projects, expanded: viewModel.projectsExpanded) {
                    withAnimation(.easeOut(duration: 0.16)) { viewModel.projectsExpanded.toggle() }
                }
                .padding(.top, 18).padding(.bottom, 6)
                if viewModel.projectsExpanded {
                    SidebarPrimaryAction(title: c.addProject, symbol: "plus") { viewModel.beginAddProject() }
                        .disabled(viewModel.state.account?.connected != true || viewModel.isUIPreview)
                        .padding(.horizontal, 8)
                        .padding(.bottom, viewModel.projects.isEmpty ? 0 : 4)
                    VStack(spacing: 2) {
                        ForEach(viewModel.projects, id: \.standardizedFileURL) { project in
                            SidebarProjectGroup(
                                project: project,
                                displayName: viewModel.projectDisplayName(project),
                                selected: viewModel.currentWorkspaceIsProject
                                    && viewModel.state.workspace?.standardizedFileURL.path == project.standardizedFileURL.path,
                                chats: viewModel.recentSessionHistory.filter {
                                    $0.isProject && $0.workspacePath == project.standardizedFileURL.path
                                },
                                allProjects: viewModel.projects,
                                labels: SidebarLabels(copy: c),
                                open: { Task { await viewModel.openProject(project) } },
                                rename: { viewModel.requestRenameProject(project) },
                                remove: { viewModel.requestRemoveProject(project) },
                                renameChat: { viewModel.requestRenameChat($0) },
                                removeChat: { viewModel.requestRemoveChat($0) },
                                moveChat: { entry, destination in _ = viewModel.moveChat(entry, toProject: destination) },
                                restoreChat: { entry in Task { await viewModel.restoreChat(entry) } },
                                projectName: { viewModel.projectDisplayName($0) }
                            )
                        }
                    }
                    .padding(.horizontal, 8)
                }

                SidebarSectionHeader(title: c.recentChats, expanded: viewModel.chatsExpanded) {
                    withAnimation(.easeOut(duration: 0.16)) { viewModel.chatsExpanded.toggle() }
                }
                .padding(.top, 18).padding(.bottom, 6)
                if viewModel.chatsExpanded {
                    SidebarPrimaryAction(title: c.newChat, symbol: "plus") {
                        Task { await viewModel.startNewChat() }
                    }
                    .disabled(viewModel.state.phase != .chatReady || viewModel.isUIPreview)
                    .padding(.horizontal, 8)
                    .padding(.bottom, generalChats.isEmpty ? 0 : 4)
                    VStack(spacing: 2) {
                        ForEach(generalChats) { entry in
                            SidebarChatHistoryRow(
                                entry: entry,
                                allProjects: viewModel.projects,
                                labels: SidebarLabels(copy: c),
                                rename: { viewModel.requestRenameChat(entry) },
                                remove: { viewModel.requestRemoveChat(entry) },
                                move: { destination in _ = viewModel.moveChat(entry, toProject: destination) },
                                restore: { Task { await viewModel.restoreChat(entry) } },
                                projectName: { viewModel.projectDisplayName($0) }
                            )
                        }
                    }
                    .padding(.horizontal, 8)
                }
            }

            Spacer(minLength: 16)
            SidebarEvidenceFooter(viewModel: viewModel, compact: compact)
                .padding(compact ? 12 : 16)
        }
        .background(MerlinTheme.panel)
    }
}

private struct SidebarLabels {
    let rename: String
    let remove: String
    let delete: String
    let moveToProject: String
    let noProjects: String

    init(copy: AppCopy) {
        rename = copy.text("Rename", "이름 변경")
        remove = copy.text("Remove", "제거")
        delete = copy.text("Delete", "삭제")
        moveToProject = copy.text("Move to project", "프로젝트로 이동")
        noProjects = copy.text("No projects available", "이동할 프로젝트가 없습니다")
    }
}

private struct SidebarSectionHeader: View {
    let title: String
    let expanded: Bool
    let action: () -> Void

    var body: some View {
        Button(action: action) {
            HStack(spacing: 7) {
                Image(systemName: expanded ? "chevron.down" : "chevron.right")
                    .font(.system(size: 9, weight: .semibold))
                    .frame(width: 10)
                Text(title.uppercased()).font(.system(size: 11, weight: .medium))
                Spacer(minLength: 0)
            }
            .foregroundStyle(MerlinTheme.muted)
            .padding(.horizontal, 16)
        }
        .buttonStyle(.plain)
    }
}

private struct SidebarProjectGroup: View {
    let project: URL
    let displayName: String
    let selected: Bool
    let chats: [LocalSessionHistoryEntry]
    let allProjects: [URL]
    let labels: SidebarLabels
    let open: () -> Void
    let rename: () -> Void
    let remove: () -> Void
    let renameChat: (LocalSessionHistoryEntry) -> Void
    let removeChat: (LocalSessionHistoryEntry) -> Void
    let moveChat: (LocalSessionHistoryEntry, URL) -> Void
    let restoreChat: (LocalSessionHistoryEntry) -> Void
    let projectName: (URL) -> String
    @State private var chatsExpanded = true

    var body: some View {
        VStack(spacing: 2) {
            SidebarProjectRow(
                displayName: displayName,
                path: project.path,
                selected: selected,
                action: open,
                rename: rename,
                remove: remove,
                labels: labels
            )
            if chatsExpanded, !chats.isEmpty {
                VStack(spacing: 2) {
                    ForEach(chats) { entry in
                        SidebarChatHistoryRow(
                            entry: entry,
                            allProjects: allProjects,
                            labels: labels,
                            rename: { renameChat(entry) },
                            remove: { removeChat(entry) },
                            move: { destination in moveChat(entry, destination) },
                            restore: { restoreChat(entry) },
                            projectName: projectName
                        )
                    }
                }
                .padding(.leading, 12)
            }
        }
    }
}

private struct SidebarProjectRow: View {
    let displayName: String
    let path: String
    let selected: Bool
    let action: () -> Void
    let rename: () -> Void
    let remove: () -> Void
    let labels: SidebarLabels

    var body: some View {
        Button(action: action) {
            HStack(spacing: 8) {
                Image(systemName: "folder.fill").font(.system(size: 12)).foregroundStyle(MerlinTheme.accent)
                Text(displayName).font(.system(size: 13, weight: .medium)).lineLimit(1)
                Spacer(minLength: 0)
            }
            .foregroundStyle(MerlinTheme.text)
            .padding(.horizontal, 10).padding(.vertical, 8)
            .frame(maxWidth: .infinity, alignment: .leading)
            .background {
                if selected { MerlinSelectionSurface(cornerRadius: 8) }
                else { MerlinTheme.elev.opacity(0.55).clipShape(RoundedRectangle(cornerRadius: 8, style: .continuous)) }
            }
        }
        .buttonStyle(.plain)
        .help(path)
        .contextMenu {
            Button(labels.rename, action: rename)
            Divider()
            Button(labels.remove, role: .destructive, action: remove)
        }
    }
}

private struct SidebarChatHistoryRow: View {
    let entry: LocalSessionHistoryEntry
    let allProjects: [URL]
    let labels: SidebarLabels
    let rename: () -> Void
    let remove: () -> Void
    let move: (URL) -> Void
    let restore: () -> Void
    let projectName: (URL) -> String

    var body: some View {
        Button(action: restore) {
            Text(entry.title)
                .font(.system(size: 13))
                .foregroundStyle(entry.isRestorable ? MerlinTheme.text : MerlinTheme.muted)
                .lineLimit(1)
                .padding(.horizontal, 10).padding(.vertical, 8)
                .frame(maxWidth: .infinity, alignment: .leading)
                .background(MerlinTheme.elev.opacity(0.55), in: RoundedRectangle(cornerRadius: 8, style: .continuous))
        }
        .buttonStyle(.plain)
        .disabled(!entry.isRestorable)
        .help(entry.isRestorable ? "" : "Saved transcript unavailable")
            .contextMenu {
                Button(labels.rename, action: rename)
                Menu(labels.moveToProject) {
                    if allProjects.isEmpty {
                        Text(labels.noProjects).disabled(true)
                    } else {
                        ForEach(allProjects, id: \.standardizedFileURL) { project in
                            Button(projectName(project)) { move(project) }
                        }
                    }
                }
                Divider()
                Button(labels.delete, role: .destructive, action: remove)
            }
    }
}

private struct SidebarPrimaryAction: View {
    let title: String
    let symbol: String
    let action: () -> Void
    @Environment(\.isEnabled) private var isEnabled

    var body: some View {
        Button(action: action) {
            HStack(spacing: 7) {
                Image(systemName: symbol).font(.system(size: 13, weight: .medium))
                Text(title).font(.system(size: 13, weight: .medium))
                Spacer(minLength: 0)
            }
            .foregroundStyle(isEnabled ? MerlinTheme.text : MerlinTheme.muted)
            .padding(.horizontal, 12)
            .frame(maxWidth: .infinity, minHeight: 34)
            .background(MerlinTheme.elev, in: RoundedRectangle(cornerRadius: 9, style: .continuous))
            .overlay { RoundedRectangle(cornerRadius: 9).stroke(MerlinTheme.border) }
        }
        .buttonStyle(.plain)
    }
}

private struct SidebarEvidenceFooter: View {
    @ObservedObject var viewModel: MerlinViewModel
    let compact: Bool

    var body: some View {
        let c = viewModel.copy
        Button { viewModel.openAccountConnectionSheet() } label: {
            Group {
            if compact {
                BrandMark(size: 18)
            } else {
                HStack(spacing: 8) {
                    BrandMark(size: 18)
                    VStack(alignment: .leading, spacing: 1) {
                        Text(viewModel.state.account?.connected == true
                             ? c.text("Codex connected", "Codex 연결됨")
                             : c.text("Codex disconnected", "Codex 연결 안 됨"))
                            .font(.system(size: 11.5, weight: .medium))
                            .foregroundStyle(MerlinTheme.text2)
                        Text(viewModel.state.providerTurnVerified
                             ? c.text("Provider verified", "공급자 확인됨")
                             : c.text("Connection status", "연결 상태"))
                            .font(.system(size: 10.5))
                            .foregroundStyle(MerlinTheme.muted)
                    }
                    Spacer(minLength: 0)
                    Circle()
                        .fill(viewModel.state.account?.connected == true ? Color.green.opacity(0.8) : MerlinTheme.muted)
                        .frame(width: 6, height: 6)
                }
                .padding(.horizontal, 9)
                .frame(maxWidth: .infinity, minHeight: 42)
                .background(MerlinTheme.elev.opacity(0.55), in: RoundedRectangle(cornerRadius: 9, style: .continuous))
                .overlay { RoundedRectangle(cornerRadius: 9).stroke(MerlinTheme.border) }
            }
        }
        }
        .buttonStyle(.plain)
        .accessibilityLabel(c.text("Open Codex connection settings", "Codex 연결 설정 열기"))
        .help(viewModel.cliEvidenceText(copy: c))
    }
}

private struct SidebarNavigationButton: View {
    let title: String
    let symbol: String
    let compact: Bool
    let active: Bool
    let action: () -> Void

    var body: some View {
        Button(action: action) {
            HStack(spacing: 9) {
                Circle().fill(active ? MerlinTheme.accent : Color.clear).frame(width: 5, height: 5)
                Image(systemName: symbol).font(.system(size: 12)).frame(width: 15)
                if !compact { Text(title).font(.system(size: 13, weight: active ? .medium : .regular)) }
                Spacer(minLength: 0)
            }
            .foregroundStyle(active ? MerlinTheme.text : MerlinTheme.text2)
            .padding(.horizontal, compact ? 0 : 8)
            .frame(maxWidth: .infinity, minHeight: 32, alignment: .leading)
            .background { if active { MerlinSelectionSurface(cornerRadius: 8) } }
        }
        .buttonStyle(.plain)
        .help(title)
    }
}

import SwiftUI

@main
struct MerlinMacApp: App {
    @StateObject private var viewModel = MerlinViewModel()

    var body: some Scene {
        WindowGroup("Merlin") {
            MerlinWindow(viewModel: viewModel)
                .task { await viewModel.bootstrap() }
        }
        .defaultSize(width: 1180, height: 760)
        .windowResizability(.contentMinSize)
        .windowStyle(.hiddenTitleBar)
        .commands {
            CommandGroup(replacing: .newItem) {
                Button(viewModel.copy.newChat) {
                    Task { await viewModel.startNewChat() }
                }
                .keyboardShortcut("n", modifiers: [.command])
            }
        }
    }
}

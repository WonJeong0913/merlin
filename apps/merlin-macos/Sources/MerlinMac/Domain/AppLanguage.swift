import Foundation

enum AppLanguage: String, CaseIterable, Identifiable {
    case korean = "ko"
    case english = "en"

    static let storageKey = "merlin.interfaceLanguage"

    var id: String { rawValue }
    var displayName: String { self == .korean ? "한국어" : "English" }

    static var deviceDefault: AppLanguage {
        Locale.preferredLanguages.first?.lowercased().hasPrefix("ko") == true ? .korean : .english
    }
}

struct AppCopy {
    let language: AppLanguage

    func text(_ english: String, _ korean: String) -> String {
        language == .korean ? korean : english
    }

    func format(_ english: String, _ korean: String, _ value: String) -> String {
        language == .korean ? String(format: korean, value) : String(format: english, value)
    }

    var appName: String { "Merlin" }
    var chat: String { text("Chat", "채팅") }
    var harnessMap: String { text("Harness Map", "하네스 맵") }
    var newChat: String { text("New chat", "새 채팅") }
    var general: String { text("General", "일반") }
    var appManagedWorkspace: String { text("App-managed workspace", "앱 관리 작업") }
    var projects: String { text("Projects", "프로젝트") }
    var addProject: String { text("Add project", "프로젝트 추가") }
    var recentChats: String { text("Chats", "채팅") }
    var session: String { text("Session", "세션") }
    var settings: String { text("Settings", "설정") }
    var workspace: String { text("Workspace", "작업 공간") }
    var send: String { text("Send", "보내기") }
    var allow: String { text("Allow", "허용") }
    var deny: String { text("Deny", "거절") }
    var refreshStatus: String { text("Refresh status", "상태 새로고침") }
    var nextSessionOnly: String { text("Applies to next session", "다음 세션에 적용") }
    var appearance: String { text("Appearance", "화면 모드") }
    var darkAppearance: String { text("Dark appearance", "다크 모드") }
    var lightAppearance: String { text("Light appearance", "라이트 모드") }
    var interfaceLanguage: String { text("Interface language", "인터페이스 언어") }
    var advanced: String { text("Advanced", "고급") }
    var model: String { text("Model", "모델") }
    var codexDefaultModel: String { text("Codex default", "Codex 기본값") }
    func modelLabel(_ modelID: String) -> String { modelID.isEmpty ? codexDefaultModel : modelID }
    var effort: String { text("Effort", "추론 수준") }
    var routing: String { text("Routing", "라우팅") }
    var autonomy: String { text("Autonomy", "자율성") }
    var codexCLIConnected: String { text("Codex CLI connected", "Codex CLI 연결됨") }
    var providerNotVerifiedYet: String { text("Provider not verified yet", "공급자 확인 전") }
    func providerTurnCompleted(model: String) -> String {
        text("Provider turn completed · requested \(model)", "공급자 턴 완료 · 요청 모델 \(model)")
    }
    var noWorkspace: String { text("No workspace", "작업 공간 없음") }
}

import AppKit
import SwiftUI

enum HarnessMapMode: String, CaseIterable, Identifiable {
    case graph = "Map"
    case skills = "Skills"
    case evidence = "Evidence"
    var id: String { rawValue }
}

/// Runtime-facing data only. Declared contracts and bridge-returned evidence stay separate.
struct HarnessMapGraphModel: Equatable {
    static let lifecycleStages = ["generation", "validation", "promotion", "selection", "feedback", "repair", "merge", "retirement"]
    static let statusOrder = ["active", "candidate", "repair", "hidden", "retired", "rejected"]

    let hooks: [HookContract]
    let skills: [SkillContract]
    let observedNodes: [HarnessNode]
    let observedEvents: [HarnessEvent]
    let recordedEvidence: [RecordedEvidence]

    init(status: SessionStatus?, evidence: HarnessMapState) {
        hooks = status?.hookContracts ?? []
        skills = status?.skillContracts ?? []
        observedNodes = evidence.nodes
        observedEvents = evidence.events
        recordedEvidence = status?.recordedEvidence ?? []
    }

    func skills(status: String? = nil, query: String = "") -> [SkillContract] {
        skills.filter { skill in
            let matchesStatus = status == nil || skill.status == status
            let haystack = "\(skill.name) \(skill.id) \(skill.description) \(skill.trigger)".lowercased()
            return matchesStatus && (query.isEmpty || haystack.contains(query.lowercased()))
        }
    }

    func matchingSkills(enabledStatuses: Set<String>, query: String) -> [SkillContract] {
        skills.filter { skill in
            let isKnownStatus = Self.statusOrder.contains(skill.status)
            let statusMatches = !isKnownStatus || enabledStatuses.contains(skill.status)
            let haystack = "\(skill.name) \(skill.id) \(skill.description) \(skill.trigger)".lowercased()
            return statusMatches && (query.isEmpty || haystack.contains(query.lowercased()))
        }
    }

    var groupedSkills: [(status: String, skills: [SkillContract])] {
        var groups: [(status: String, skills: [SkillContract])] = []
        for status in Self.statusOrder {
            let matching = skills.filter { $0.status == status }
            if !matching.isEmpty { groups.append((status, matching)) }
        }
        let extras = Dictionary(grouping: skills.filter { !Self.statusOrder.contains($0.status) }, by: \.status)
        for status in extras.keys.sorted() {
            groups.append((status, extras[status] ?? []))
        }
        return groups
    }
}

/// Pure spatial layout used by Graph mode and unit tests.
struct HarnessGraphLayout: Equatable {
    let canvasSize: CGSize
    let root: CGPoint
    let library: CGPoint
    let hooks: [String: CGPoint]
    let skills: [String: CGPoint]
    let evidence: [String: CGPoint]
    let lifecycle: [String: CGPoint]

    init(model: HarnessMapGraphModel, viewportSize: CGSize = CGSize(width: 1_000, height: 680)) {
        let width = max(720, viewportSize.width)
        let height = max(520, viewportSize.height)
        root = CGPoint(x: width / 2, y: 56)
        library = CGPoint(x: width / 2, y: min(300, height * 0.43))
        let hookMargin: CGFloat = 82
        let hookStep = (width - hookMargin * 2) / 3
        let hookPoints = model.hooks.enumerated().map { index, hook in
            (hook.id, CGPoint(x: hookMargin + CGFloat(index % 4) * hookStep, y: 142 + CGFloat(index / 4) * 78))
        }
        hooks = Dictionary(uniqueKeysWithValues: hookPoints)
        var skillPoints: [String: CGPoint] = [:]
        let skillCount = max(model.skills.count, 1)
        let skillSpan = min(width - 150, CGFloat(skillCount - 1) * 138)
        let skillStart = (width - skillSpan) / 2
        for (index, skill) in model.skills.enumerated() {
            skillPoints[skill.id] = CGPoint(
                x: skillCount == 1 ? width / 2 : skillStart + CGFloat(index) * skillSpan / CGFloat(skillCount - 1),
                y: min(height - 165, library.y + 115 + CGFloat(index / 6) * 82)
            )
        }
        skills = skillPoints
        let evidenceY = max(library.y + 210, height - 130)
        evidence = Dictionary(uniqueKeysWithValues: model.observedNodes.enumerated().map { index, node in
            (node.id, CGPoint(x: 100 + CGFloat(index % 5) * max(120, (width - 200) / 4), y: evidenceY - CGFloat(index / 5) * 70))
        })
        let lifecycleMargin: CGFloat = 78
        lifecycle = Dictionary(uniqueKeysWithValues: HarnessMapGraphModel.lifecycleStages.enumerated().map { index, stage in
            (stage, CGPoint(x: lifecycleMargin + CGFloat(index) * (width - lifecycleMargin * 2) / 7, y: height - 50))
        })
        canvasSize = CGSize(width: width, height: height)
    }
}

struct HarnessMapView: View {
    let status: SessionStatus?
    let evidence: HarnessMapState
    @Binding var mode: HarnessMapMode
    @State private var selection: HarnessMapSelection?
    @State private var zoom: CGFloat = 1
    @GestureState private var pinchZoom: CGFloat = 1
    @State private var nodeOffsets: [String: CGSize] = [:]
    @State private var skillQuery = ""
    @State private var enabledStatuses = Set(HarnessMapGraphModel.statusOrder)

    private var model: HarnessMapGraphModel { HarnessMapGraphModel(status: status, evidence: evidence) }

    var body: some View {
        ZStack(alignment: .trailing) {
            VStack(spacing: 0) {
                toolbar
                Divider().overlay(MerlinTheme.border)
                modeBody
                footer
            }
            if selection != nil {
                HarnessNodeDetailPage(selection: selection, model: model) { selection = nil }
                    .id(selection?.id)
                    .frame(width: 360)
                    .background(MerlinTheme.panel)
                    .overlay(alignment: .leading) { Rectangle().fill(MerlinTheme.border).frame(width: 1) }
                    .shadow(color: .black.opacity(0.22), radius: 20, x: -7)
                    .transition(.move(edge: .trailing).combined(with: .opacity))
            }
        }
        .background(MerlinTheme.bg)
        .animation(.easeOut(duration: 0.18), value: selection?.id)
        .onChange(of: mode) { _ in selection = nil }
    }

    private var toolbar: some View {
        HStack(spacing: 12) {
            VStack(alignment: .leading, spacing: 2) {
                Text("Harness Map").font(.system(size: 14, weight: .medium))
                Text("Runtime contracts and returned evidence only").font(.system(size: 11)).foregroundStyle(MerlinTheme.muted)
            }
            Picker("Map mode", selection: $mode) {
                ForEach(HarnessMapMode.allCases) { Text($0.rawValue).tag($0) }
            }
            .pickerStyle(.segmented)
            .frame(width: 280)
            .accessibilityLabel("Harness Map mode")
            Spacer()
            if mode == .graph { zoomControls }
        }
        .padding(.horizontal, 16).frame(height: 54)
    }

    @ViewBuilder private var modeBody: some View {
        switch mode {
        case .graph: graphMode
        case .skills: HarnessMapSkills(model: model, selection: $selection, query: $skillQuery, enabledStatuses: $enabledStatuses)
        case .evidence: HarnessRecordedEvidenceList(model: model, selection: $selection)
        }
    }

    private var graphMode: some View {
        GeometryReader { viewport in
            let layout = HarnessGraphLayout(model: model, viewportSize: viewport.size)
            ZStack(alignment: .topLeading) {
                GraphEdges(model: model, layout: layout, offsets: nodeOffsets)
                InteractiveGraphNode(
                    id: "root", position: layout.root, title: "Merlin Harness", subtitle: "harness root",
                    symbol: "camera.macro", tint: MerlinTheme.accent, size: 50, dashed: false,
                    offset: nodeOffsetBinding("root")
                ) { selection = .root }
                InteractiveGraphNode(
                    id: "library", position: layout.library, title: "Skill Library", subtitle: "\(model.skills.count) contracts",
                    symbol: "books.vertical", tint: MerlinTheme.accent, size: 44, dashed: false,
                    offset: nodeOffsetBinding("library")
                ) { selection = .library }
                ForEach(model.hooks) { hook in
                    if let point = layout.hooks[hook.id] {
                        InteractiveGraphNode(
                            id: "hook:\(hook.id)", position: point, title: hook.hook, subtitle: "HarnessX hook",
                            symbol: "circle.hexagongrid", tint: MerlinTheme.accent, size: 35, dashed: true,
                            offset: nodeOffsetBinding("hook:\(hook.id)")
                        ) { selection = .hook(hook) }
                    }
                }
                ForEach(model.skills) { skill in
                    if let point = layout.skills[skill.id] {
                        InteractiveGraphNode(
                            id: "skill:\(skill.id)", position: point, title: skill.name, subtitle: skill.status,
                            symbol: "diamond.fill", tint: SkillStatusColor.value(for: skill.status), size: 40, dashed: false,
                            offset: nodeOffsetBinding("skill:\(skill.id)")
                        ) { selection = .skill(skill) }
                    }
                }
                ForEach(model.observedNodes) { node in
                    if let point = layout.evidence[node.id] {
                        InteractiveGraphNode(
                            id: "evidence:\(node.id)", position: point, title: node.label, subtitle: "observed evidence",
                            symbol: node.kind == .turn ? "bubble.left.fill" : "checkmark.shield.fill",
                            tint: evidenceTint(node), size: 37, dashed: false,
                            offset: nodeOffsetBinding("evidence:\(node.id)")
                        ) { selection = .evidence(node) }
                    }
                }
                ForEach(HarnessMapGraphModel.lifecycleStages, id: \.self) { stage in
                    if let point = layout.lifecycle[stage] {
                        let observed = lifecycleObserved(stage)
                        LifecycleGraphNode(
                            stage: stage,
                            observed: observed,
                            position: point,
                            selection: $selection
                        )
                        .id("lifecycle:\(stage)")
                    }
                }
            }
            .frame(width: layout.canvasSize.width, height: layout.canvasSize.height, alignment: .topLeading)
            .scaleEffect(zoom * pinchZoom, anchor: .center)
            .contentShape(Rectangle())
            .gesture(graphMagnificationGesture)
            .background(MouseWheelZoomCapture { delta in
                guard abs(delta) > 0.15 else { return }
                let factor = exp(delta * 0.0025)
                zoom = min(2.0, max(0.55, zoom * factor))
            })
            .onAppear { resetGraphView() }
        }
        .clipped()
        .background(RadialGradient(colors: [MerlinTheme.accentWeak.opacity(0.5), MerlinTheme.bg], center: .top, startRadius: 0, endRadius: 760))
    }

    private func resetGraphView() {
        zoom = 1
    }

    private func nodeOffsetBinding(_ id: String) -> Binding<CGSize> {
        Binding(
            get: { nodeOffsets[id] ?? .zero },
            set: { nodeOffsets[id] = $0 }
        )
    }

    private var graphMagnificationGesture: some Gesture {
        MagnificationGesture()
            .updating($pinchZoom) { value, state, _ in state = value }
            .onEnded { value in zoom = min(2.0, max(0.55, zoom * value)) }
    }

    private var zoomControls: some View {
        HStack(spacing: 5) {
            Button { zoom = max(0.55, zoom - 0.1) } label: { Image(systemName: "minus") }
            Button(action: resetGraphView) { Text("\(Int(zoom * 100))%").frame(minWidth: 38) }
                .accessibilityLabel("Reset graph zoom to 100 percent")
            Button { zoom = min(2.0, zoom + 0.1) } label: { Image(systemName: "plus") }
        }
        .font(.system(size: 11, weight: .medium))
        .buttonStyle(.plain)
        .padding(4)
        .background(MerlinTheme.elev, in: RoundedRectangle(cornerRadius: 7))
        .overlay { RoundedRectangle(cornerRadius: 7).stroke(MerlinTheme.border) }
        .accessibilityElement(children: .contain)
        .accessibilityLabel("Graph zoom controls")
    }

    private var footer: some View {
        HStack(spacing: 6) {
            Circle().fill(MerlinTheme.accent).frame(width: 6, height: 6)
            Text("Dotted lifecycle nodes are possible/declared states. Solid nodes require returned observed evidence.")
                .font(.system(size: 11)).foregroundStyle(MerlinTheme.muted)
        }
        .padding(.horizontal, 14).frame(height: 36).frame(maxWidth: .infinity, alignment: .leading)
        .background(MerlinTheme.panel)
    }

    private func lifecycleObserved(_ stage: String) -> Bool {
        model.observedNodes.contains { $0.kind == .action && $0.label.lowercased().contains(stage) }
            || model.observedEvents.contains { $0.kind == .lifecycleEvent && $0.title.lowercased().contains(stage) }
    }

    private func evidenceTint(_ node: HarnessNode) -> Color {
        node.evidenceKind == .lifecycleEvent ? MerlinTheme.red : MerlinTheme.green
    }
}

private enum HarnessMapSelection: Identifiable {
    case root, library
    case hook(HookContract), skill(SkillContract), evidence(HarnessNode)
    case recorded(RecordedEvidence)
    case lifecycle(String, observed: Bool)
    var id: String {
        switch self {
        case .root: return "root"
        case .library: return "library"
        case let .hook(value): return "hook-\(value.id)"
        case let .skill(value): return "skill-\(value.id)"
        case let .evidence(value): return "evidence-\(value.id)"
        case let .recorded(value): return "recorded-\(value.id)"
        case let .lifecycle(stage, observed): return "lifecycle-\(stage)-\(observed)"
        }
    }
}

/// Owns one immutable lifecycle identity and exposes a real native Button.
/// This keeps mouse and accessibility actions distinct for all eight stages.
private struct LifecycleGraphNode: View {
    let stage: String
    let observed: Bool
    let position: CGPoint
    @Binding var selection: HarnessMapSelection?
    @State private var hovered = false

    var body: some View {
        let tint = observed ? MerlinTheme.green : MerlinTheme.muted
        Button {
            selection = .lifecycle(stage, observed: observed)
        } label: {
            VStack(spacing: 5) {
                ZStack {
                    Circle()
                        .fill(MerlinTheme.elev.opacity(0.96))
                        .frame(width: 28, height: 28)
                        .overlay {
                            Circle().stroke(
                                tint.opacity(hovered ? 0.95 : 0.65),
                                style: StrokeStyle(lineWidth: hovered ? 2 : 1.2, dash: observed ? [] : [4, 3])
                            )
                        }
                        .shadow(color: tint.opacity(hovered ? 0.46 : 0.18), radius: hovered ? 12 : 5)
                    Circle().fill(tint.opacity(hovered ? 0.18 : 0.08)).frame(width: 20, height: 20)
                    Image(systemName: observed ? "checkmark.circle.fill" : "circle.dashed")
                        .font(.system(size: 10, weight: .medium))
                        .foregroundStyle(tint)
                }
                Text(stage)
                    .font(.system(size: 10.5, weight: .medium))
                    .foregroundStyle(MerlinTheme.text2)
                    .lineLimit(1)
                Text(observed ? "observed" : "possible")
                    .font(.system(size: 8.5))
                    .foregroundStyle(MerlinTheme.muted)
                    .lineLimit(1)
            }
            .frame(width: 116, height: 78)
            .contentShape(Rectangle())
        }
        .buttonStyle(.plain)
        .scaleEffect(hovered ? 1.06 : 1)
        // `position` adopts the full proposed canvas size inside a ZStack and
        // makes the last node intercept pointer events across the graph. A
        // fixed-size node plus offset keeps hit testing on the visible card.
        .offset(x: position.x - 58, y: position.y - 39)
        .onHover { value in withAnimation(.easeOut(duration: 0.14)) { hovered = value } }
        .accessibilityLabel("\(stage), \(observed ? "observed" : "possible")")
        .accessibilityHint("Click for details.")
    }
}

private struct GraphEdges: View {
    let model: HarnessMapGraphModel
    let layout: HarnessGraphLayout
    let offsets: [String: CGSize]
    var body: some View {
        Canvas { context, _ in
            let root = moved(layout.root, id: "root")
            let library = moved(layout.library, id: "library")
            for hook in model.hooks {
                guard let point = layout.hooks[hook.id] else { continue }
                let target = moved(point, id: "hook:\(hook.id)")
                arrow(context: &context, from: root, to: target, color: MerlinTheme.accent, dashed: true)
                arrow(context: &context, from: target, to: library, color: MerlinTheme.accent, dashed: true)
            }
            for skill in model.skills {
                guard let point = layout.skills[skill.id] else { continue }
                arrow(context: &context, from: library, to: moved(point, id: "skill:\(skill.id)"), color: MerlinTheme.border, dashed: false)
            }
            for node in model.observedNodes {
                guard let point = layout.evidence[node.id] else { continue }
                arrow(context: &context, from: library, to: moved(point, id: "evidence:\(node.id)"), color: MerlinTheme.green, dashed: false)
            }
            let lifecyclePoints = HarnessMapGraphModel.lifecycleStages.compactMap { stage in
                layout.lifecycle[stage].map { moved($0, id: "lifecycle:\(stage)") }
            }
            for (source, target) in zip(lifecyclePoints, lifecyclePoints.dropFirst()) {
                arrow(context: &context, from: source, to: target, color: MerlinTheme.muted, dashed: true)
            }
        }
        .frame(width: layout.canvasSize.width, height: layout.canvasSize.height)
        .allowsHitTesting(false)
    }

    private func moved(_ point: CGPoint, id: String) -> CGPoint {
        let offset = offsets[id] ?? .zero
        return CGPoint(x: point.x + offset.width, y: point.y + offset.height)
    }
    private func arrow(context: inout GraphicsContext, from: CGPoint, to: CGPoint, color: Color, dashed: Bool) {
        var path = Path(); path.move(to: from); path.addLine(to: to)
        context.stroke(path, with: .color(color.opacity(0.55)), style: StrokeStyle(lineWidth: 1.15, dash: dashed ? [4, 4] : []))
        let angle = atan2(to.y - from.y, to.x - from.x)
        var tip = Path(); tip.move(to: to)
        tip.addLine(to: CGPoint(x: to.x - 7 * cos(angle - .pi / 6), y: to.y - 7 * sin(angle - .pi / 6)))
        tip.move(to: to)
        tip.addLine(to: CGPoint(x: to.x - 7 * cos(angle + .pi / 6), y: to.y - 7 * sin(angle + .pi / 6)))
        context.stroke(tip, with: .color(color.opacity(0.7)), lineWidth: 1.15)
    }
}

private struct InteractiveGraphNode: View {
    let id: String
    let position: CGPoint
    let title: String
    let subtitle: String
    let symbol: String
    let tint: Color
    let size: CGFloat
    let dashed: Bool
    @Binding var offset: CGSize
    var isDraggable = true
    let action: () -> Void

    @State private var hovered = false
    @State private var dragOrigin: CGSize?

    var body: some View {
        Button(action: action) {
            VStack(spacing: 5) {
                ZStack {
                    Circle()
                        .fill(MerlinTheme.elev.opacity(0.96))
                        .frame(width: size, height: size)
                        .overlay {
                            Circle().stroke(
                                tint.opacity(hovered ? 0.95 : 0.65),
                                style: StrokeStyle(lineWidth: hovered ? 2 : 1.2, dash: dashed ? [4, 3] : [])
                            )
                        }
                        .shadow(color: tint.opacity(hovered ? 0.46 : 0.18), radius: hovered ? 12 : 5)
                    Circle().fill(tint.opacity(hovered ? 0.18 : 0.08)).frame(width: size * 0.72, height: size * 0.72)
                    Image(systemName: symbol)
                        .font(.system(size: max(10, size * 0.30), weight: .medium))
                        .foregroundStyle(tint)
                }
                Text(title)
                    .font(.system(size: 10.5, weight: .medium))
                    .foregroundStyle(MerlinTheme.text2)
                    .lineLimit(1)
                Text(subtitle)
                    .font(.system(size: 8.5))
                    .foregroundStyle(MerlinTheme.muted)
                    .lineLimit(1)
            }
            .frame(width: 116, height: 78)
            .contentShape(Rectangle())
        }
        .buttonStyle(.plain)
        .simultaneousGesture(
            DragGesture(minimumDistance: 3)
                .onChanged { value in
                    guard isDraggable else { return }
                    if dragOrigin == nil { dragOrigin = offset }
                    let origin = dragOrigin ?? offset
                    offset = CGSize(
                        width: origin.width + value.translation.width,
                        height: origin.height + value.translation.height
                    )
                }
                .onEnded { _ in dragOrigin = nil }
        )
        .scaleEffect(hovered ? 1.06 : 1)
        .offset(
            x: position.x + offset.width - 58,
            y: position.y + offset.height - 39
        )
        .onHover { value in withAnimation(.easeOut(duration: 0.14)) { hovered = value } }
        .accessibilityLabel("\(title), \(subtitle)")
        .accessibilityHint(isDraggable ? "Click for details. Drag to move this node." : "Click for details.")
    }
}

private struct HarnessMapSkills: View {
    let model: HarnessMapGraphModel
    @Binding var selection: HarnessMapSelection?
    @Binding var query: String
    @Binding var enabledStatuses: Set<String>
    var body: some View {
        VStack(spacing: 0) {
            HStack(spacing: 8) {
                TextField("Search skill contracts", text: $query).textFieldStyle(.roundedBorder).accessibilityLabel("Search skill contracts")
                ForEach(HarnessMapGraphModel.statusOrder, id: \.self) { status in
                    Button(status) { if enabledStatuses.contains(status) { enabledStatuses.remove(status) } else { enabledStatuses.insert(status) } }
                        .buttonStyle(.plain).font(.system(size: 10.5, weight: .medium))
                        .padding(.horizontal, 7).padding(.vertical, 5)
                        .background(enabledStatuses.contains(status) ? SkillStatusColor.value(for: status).opacity(0.18) : MerlinTheme.elev, in: Capsule())
                        .overlay { Capsule().stroke(enabledStatuses.contains(status) ? SkillStatusColor.value(for: status) : MerlinTheme.border) }
                        .accessibilityLabel("Filter \(status)")
                }
            }
            .padding(16)
            Divider().overlay(MerlinTheme.border)
            let filtered = model.matchingSkills(enabledStatuses: enabledStatuses, query: query)
            ScrollView {
                LazyVGrid(columns: [GridItem(.adaptive(minimum: 210), spacing: 10)], spacing: 10) {
                    ForEach(filtered) { skill in
                        Button { selection = .skill(skill) } label: { SkillCard(skill: skill) }.buttonStyle(.plain)
                    }
                }.padding(20)
            }
        }
    }
}

private struct SkillCard: View {
    let skill: SkillContract
    var body: some View {
        VStack(alignment: .leading, spacing: 6) {
            HStack { Text(skill.name).font(.system(size: 12.5, weight: .medium)); Spacer(); Text(skill.status).font(.system(size: 9.5, weight: .medium)).foregroundStyle(SkillStatusColor.value(for: skill.status)) }
            Text(skill.trigger).font(.system(size: 10.5)).foregroundStyle(MerlinTheme.muted).lineLimit(2)
            Text("v\(skill.version) · \(skill.stepCount) steps · \(skill.validators.count) validators").font(.system(size: 10)).foregroundStyle(MerlinTheme.muted)
        }
        .padding(11).frame(maxWidth: .infinity, minHeight: 88, alignment: .leading)
        .background(MerlinTheme.elev, in: RoundedRectangle(cornerRadius: 9))
        .overlay { RoundedRectangle(cornerRadius: 9).stroke(SkillStatusColor.value(for: skill.status).opacity(0.45)) }
    }
}

private struct HarnessRecordedEvidenceList: View {
    let model: HarnessMapGraphModel
    @Binding var selection: HarnessMapSelection?

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 16) {
                VStack(alignment: .leading, spacing: 5) {
                    Label("Recorded Evidence", systemImage: "checkmark.seal")
                        .font(.system(size: 17, weight: .semibold))
                    Text("Hash-bound retained campaigns. These records are separate from the active runtime map.")
                        .font(.system(size: 11.5))
                        .foregroundStyle(MerlinTheme.muted)
                }
                if model.recordedEvidence.isEmpty {
                    Text("No validated recorded evidence was returned by the local bridge.")
                        .font(.system(size: 12)).foregroundStyle(MerlinTheme.muted)
                        .padding(.top, 22)
                } else {
                    LazyVGrid(columns: [GridItem(.adaptive(minimum: 250), spacing: 12)], spacing: 12) {
                        ForEach(model.recordedEvidence) { record in
                            Button { selection = .recorded(record) } label: {
                                RecordedEvidenceCard(record: record)
                            }
                            .buttonStyle(.plain)
                        }
                    }
                }
            }
            .padding(22)
            .frame(maxWidth: .infinity, alignment: .leading)
        }
    }
}

private struct RecordedEvidenceCard: View {
    let record: RecordedEvidence

    private var tint: Color { RecordedEvidenceTint.value(for: record.status) }

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            HStack(alignment: .top, spacing: 9) {
                Image(systemName: record.actualProviderRun ? "bolt.shield.fill" : "checkmark.shield")
                    .foregroundStyle(tint)
                    .frame(width: 24, height: 24)
                    .background(tint.opacity(0.10), in: RoundedRectangle(cornerRadius: 7))
                VStack(alignment: .leading, spacing: 2) {
                    Text(record.title).font(.system(size: 12.5, weight: .semibold)).lineLimit(1)
                    Text(record.kind).font(.system(size: 10)).foregroundStyle(MerlinTheme.muted).lineLimit(1)
                }
                Spacer(minLength: 4)
                Text(record.status.uppercased())
                    .font(.system(size: 8.5, weight: .semibold)).foregroundStyle(tint)
            }
            Text(record.role)
                .font(.system(size: 10.5)).foregroundStyle(MerlinTheme.text2)
                .lineLimit(3).multilineTextAlignment(.leading)
            HStack {
                Text("\(record.gatesPassed)/\(record.gatesTotal) gates")
                Spacer()
                Text(record.actualProviderRun ? "provider run" : "controlled fixture")
            }
            .font(.system(size: 9.5)).foregroundStyle(MerlinTheme.muted)
        }
        .padding(13)
        .frame(maxWidth: .infinity, minHeight: 132, alignment: .topLeading)
        .background(MerlinTheme.elev, in: RoundedRectangle(cornerRadius: 12))
        .overlay { RoundedRectangle(cornerRadius: 12).stroke(tint.opacity(0.38)) }
    }
}

private struct HarnessNodeDetailPage: View {
    let selection: HarnessMapSelection?
    let model: HarnessMapGraphModel
    let close: () -> Void

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 18) {
                HStack {
                    Text("DETAIL")
                        .font(.system(size: 10.5, weight: .semibold))
                        .tracking(0.8)
                        .foregroundStyle(MerlinTheme.muted)
                    Spacer()
                    Button(action: close) {
                        Image(systemName: "xmark")
                            .font(.system(size: 11, weight: .semibold))
                            .frame(width: 26, height: 26)
                            .background(MerlinTheme.elev, in: Circle())
                    }
                    .buttonStyle(.plain)
                    .accessibilityLabel("Close detail")
                }
                switch selection {
                case .root:
                    genericDetail(
                        title: "Merlin Harness", status: "runtime root", symbol: "camera.macro",
                        role: "Coordinates skill generation, validation, provisioning, selection, feedback, and lifecycle policy."
                    )
                case .library:
                    genericDetail(
                        title: "Skill Library", status: "\(model.skills.count) contracts", symbol: "books.vertical",
                        role: "The managed contract registry currently returned by session.status."
                    )
                case let .hook(hook): hookDetail(hook)
                case let .skill(skill): skillDetail(skill)
                case let .evidence(node): evidenceDetail(node)
                case let .recorded(record): recordedDetail(record)
                case let .lifecycle(stage, observed): lifecycleDetail(stage, observed: observed)
                case .none: EmptyView()
                }
                Divider().overlay(MerlinTheme.border)
                Label(
                    "Provider-native invocation and lifecycle completion are shown only when returned evidence exists.",
                    systemImage: "info.circle"
                )
                .font(.system(size: 9.5))
                .foregroundStyle(MerlinTheme.muted)
                .fixedSize(horizontal: false, vertical: true)
            }
            .padding(20)
        }
        .background(MerlinTheme.panel)
    }

    private func detailHeader(title: String, status: String, symbol: String, tint: Color) -> some View {
        VStack(alignment: .leading, spacing: 11) {
            ZStack {
                RoundedRectangle(cornerRadius: 12).fill(tint.opacity(0.12))
                Image(systemName: symbol).font(.system(size: 19, weight: .medium)).foregroundStyle(tint)
            }
            .frame(width: 46, height: 46)
            Text(title)
                .font(.system(size: 20, weight: .semibold))
                .foregroundStyle(MerlinTheme.text)
                .textSelection(.enabled)
            Text(status.uppercased())
                .font(.system(size: 9.5, weight: .semibold))
                .tracking(0.6)
                .foregroundStyle(tint)
                .padding(.horizontal, 8).padding(.vertical, 4)
                .background(tint.opacity(0.10), in: Capsule())
        }
    }

    private func card<Content: View>(_ title: String, @ViewBuilder content: () -> Content) -> some View {
        VStack(alignment: .leading, spacing: 9) {
            Text(title.uppercased())
                .font(.system(size: 9.5, weight: .semibold))
                .tracking(0.65)
                .foregroundStyle(MerlinTheme.muted)
            content()
                .font(.system(size: 11.5))
                .foregroundStyle(MerlinTheme.text2)
                .lineSpacing(2)
        }
        .padding(13)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(MerlinTheme.elev.opacity(0.72), in: RoundedRectangle(cornerRadius: 11))
        .overlay { RoundedRectangle(cornerRadius: 11).stroke(MerlinTheme.border) }
    }

    private func genericDetail(title: String, status: String, symbol: String, role: String) -> some View {
        VStack(alignment: .leading, spacing: 16) {
            detailHeader(title: title, status: status, symbol: symbol, tint: MerlinTheme.accent)
            card("Role") { Text(role) }
            card("Hooks & validators") {
                Text("\(model.hooks.count) declared hooks · \(model.skills.reduce(0) { $0 + $1.validators.count }) validators")
            }
            lifecycleCard(matching: title)
        }
    }

    private func skillDetail(_ skill: SkillContract) -> some View {
        VStack(alignment: .leading, spacing: 16) {
            detailHeader(
                title: skill.name,
                status: "\(skill.status) · v\(skill.version)",
                symbol: "diamond.fill",
                tint: SkillStatusColor.value(for: skill.status)
            )
            card("Role") {
                VStack(alignment: .leading, spacing: 8) {
                    Text(skill.description)
                    Divider().overlay(MerlinTheme.border)
                    Label(skill.trigger, systemImage: "scope")
                    Text("\(skill.stepCount) steps · \(skill.edgeCount) edges")
                        .font(.system(size: 10)).foregroundStyle(MerlinTheme.muted)
                }
            }
            card("Hooks & validators") {
                VStack(alignment: .leading, spacing: 9) {
                    Label("Managed through \(model.hooks.count) declared harness hooks", systemImage: "point.3.filled.connected.trianglepath.dotted")
                    Divider().overlay(MerlinTheme.border)
                    if skill.validators.isEmpty {
                        Text("No validator returned")
                    } else {
                        ForEach(skill.validators, id: \.self) { value in Label(value, systemImage: "checkmark.shield") }
                    }
                }
            }
            card("Contract outputs") {
                VStack(alignment: .leading, spacing: 7) {
                    Text(skill.expectedArtifacts.isEmpty ? "No expected artifact returned" : skill.expectedArtifacts.joined(separator: "\n"))
                    if !skill.failureModes.isEmpty {
                        Divider().overlay(MerlinTheme.border)
                        Text("Failure modes").font(.system(size: 10, weight: .semibold)).foregroundStyle(MerlinTheme.muted)
                        Text(skill.failureModes.joined(separator: "\n"))
                    }
                }
            }
            lifecycleCard(matching: skill.name, alternate: skill.id)
        }
    }

    private func hookDetail(_ hook: HookContract) -> some View {
        VStack(alignment: .leading, spacing: 16) {
            detailHeader(title: hook.hook, status: "declared hook", symbol: "circle.hexagongrid", tint: MerlinTheme.accent)
            card("Role") { Text("Harness policy interception point with bounded permitted mutations.") }
            card("Hook contract") {
                Text(hook.permittedMutations.isEmpty ? "Read-only · no mutation" : hook.permittedMutations.joined(separator: "\n"))
            }
            lifecycleCard(matching: hook.hook)
        }
    }

    private func evidenceDetail(_ node: HarnessNode) -> some View {
        VStack(alignment: .leading, spacing: 16) {
            detailHeader(title: node.label, status: "observed \(node.evidenceKind.rawValue)", symbol: "checkmark.shield.fill", tint: MerlinTheme.green)
            card("Role") { Text("Returned runtime evidence associated with a \(node.kind.rawValue) node.") }
            card("Trace") { Text(node.tracePointer ?? "No trace pointer returned") }
        }
    }

    private func recordedDetail(_ record: RecordedEvidence) -> some View {
        let tint = RecordedEvidenceTint.value(for: record.status)
        return VStack(alignment: .leading, spacing: 16) {
            detailHeader(
                title: record.title,
                status: "\(record.status) · recorded evidence",
                symbol: record.actualProviderRun ? "bolt.shield.fill" : "checkmark.shield",
                tint: tint
            )
            card("Role") { Text(record.role) }
            card("Hooks & validators") {
                VStack(alignment: .leading, spacing: 8) {
                    Text("\(record.gatesPassed)/\(record.gatesTotal) validation gates passed")
                    Label(
                        record.actualProviderRun ? "Actual provider run retained" : "Controlled deterministic fixture",
                        systemImage: record.actualProviderRun ? "bolt.fill" : "slider.horizontal.3"
                    )
                    Label(
                        record.providerNativeInvocation ? "Native invocation observed" : "Native invocation not claimed",
                        systemImage: record.providerNativeInvocation ? "checkmark.circle.fill" : "minus.circle"
                    )
                    if let requestedModel = record.requestedModel {
                        Text("Requested model: \(requestedModel)")
                    }
                    Text(record.modelEvidenceLevel.replacingOccurrences(of: "_", with: " "))
                        .font(.system(size: 10)).foregroundStyle(MerlinTheme.muted)
                }
            }
            card("Lifecycle record") {
                VStack(alignment: .leading, spacing: 0) {
                    ForEach(Array(record.lifecycle.enumerated()), id: \.offset) { index, stage in
                        HStack(alignment: .top, spacing: 9) {
                            VStack(spacing: 0) {
                                Circle().fill(index == record.lifecycle.count - 1 ? tint : MerlinTheme.accent)
                                    .frame(width: 7, height: 7)
                                if index < record.lifecycle.count - 1 {
                                    Rectangle().fill(MerlinTheme.border).frame(width: 1, height: 18)
                                }
                            }
                            Text(stage.capitalized).padding(.top, -4)
                        }
                    }
                }
            }
            card("Evidence source") {
                VStack(alignment: .leading, spacing: 5) {
                    Text(record.sourcePath).textSelection(.enabled)
                    Text(record.sourceSHA256)
                        .font(.system(size: 9.5, design: .monospaced))
                        .foregroundStyle(MerlinTheme.muted)
                        .textSelection(.enabled)
                }
            }
        }
    }

    private func lifecycleDetail(_ stage: String, observed: Bool) -> some View {
        VStack(alignment: .leading, spacing: 16) {
            detailHeader(
                title: stage.capitalized,
                status: observed ? "observed" : "possible · not observed",
                symbol: observed ? "checkmark.circle.fill" : "circle.dashed",
                tint: observed ? MerlinTheme.green : MerlinTheme.muted
            )
            card("Role") { Text("One stage in the managed skill lifecycle.") }
            card("Lifecycle record") {
                Text(observed ? "Returned evidence exists for this lifecycle stage." : "No completed action is claimed for this stage.")
            }
        }
    }

    private func lifecycleCard(matching primary: String, alternate: String = "") -> some View {
        let needles = [primary, alternate].filter { !$0.isEmpty }.map { $0.lowercased() }
        let events = model.observedEvents.filter { event in
            let haystack = "\(event.title) \(event.detail)".lowercased()
            return needles.contains { haystack.contains($0) }
        }
        return card("Lifecycle record") {
            VStack(alignment: .leading, spacing: 8) {
                if events.isEmpty {
                    Label("No observed lifecycle event", systemImage: "clock.badge.questionmark")
                        .foregroundStyle(MerlinTheme.muted)
                } else {
                    ForEach(events) { event in
                        VStack(alignment: .leading, spacing: 2) {
                            Text(event.title).font(.system(size: 11.5, weight: .medium))
                            Text(event.detail).font(.system(size: 10)).foregroundStyle(MerlinTheme.muted)
                        }
                    }
                }
            }
        }
    }
}

private enum RecordedEvidenceTint {
    static func value(for status: String) -> Color {
        switch status {
        case "promoted", "merged": MerlinTheme.green
        case "rolled back": .orange
        default: MerlinTheme.accent
        }
    }
}

/// Captures native macOS wheel events only while the pointer is inside the graph.
/// It does not participate in hit testing, so node clicks and drags remain native SwiftUI gestures.
private struct MouseWheelZoomCapture: NSViewRepresentable {
    let onScroll: (CGFloat) -> Void

    func makeCoordinator() -> Coordinator { Coordinator(onScroll: onScroll) }

    func makeNSView(context: Context) -> NSView {
        let view = NSView(frame: .zero)
        context.coordinator.view = view
        context.coordinator.installMonitor()
        return view
    }

    func updateNSView(_ nsView: NSView, context: Context) {
        context.coordinator.view = nsView
        context.coordinator.onScroll = onScroll
    }

    static func dismantleNSView(_ nsView: NSView, coordinator: Coordinator) {
        coordinator.removeMonitor()
    }

    @MainActor
    final class Coordinator {
        weak var view: NSView?
        var onScroll: (CGFloat) -> Void
        private var monitor: Any?

        init(onScroll: @escaping (CGFloat) -> Void) {
            self.onScroll = onScroll
        }

        func installMonitor() {
            guard monitor == nil else { return }
            monitor = NSEvent.addLocalMonitorForEvents(matching: .scrollWheel) { [weak self] event in
                guard let self, let view = self.view,
                      event.window === view.window,
                      view.bounds.contains(view.convert(event.locationInWindow, from: nil)) else {
                    return event
                }
                self.onScroll(event.scrollingDeltaY)
                return nil
            }
        }

        func removeMonitor() {
            if let monitor { NSEvent.removeMonitor(monitor) }
            monitor = nil
        }

    }
}

private enum SkillStatusColor {
    static func value(for status: String) -> Color { switch status { case "active": MerlinTheme.green; case "candidate": MerlinTheme.accent; case "repair": .orange; case "hidden": MerlinTheme.muted; case "retired": .gray; case "rejected": MerlinTheme.red; default: MerlinTheme.text2 } }
}

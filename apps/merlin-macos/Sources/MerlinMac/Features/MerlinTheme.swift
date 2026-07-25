import AppKit
import SwiftUI

enum MerlinTheme {
    // Every chromatic value below is sampled from the liquid-glass flower mark
    // in `AppBundle/Resources/Branding/merlin-flower-liquid-glass.png` rather
    // than picked by eye. Measured over its opaque, chromatic pixels
    // (saturation >= 0.22, n = 28,451): hue spans 280–339 with the mode at
    // 320–329 and the median at 317; median saturation 0.33, value 0.91; the
    // deepest petal tone is #751A73 (H 301, S 0.78, V 0.46).
    //
    // Light is the primary appearance. Dark is supported but secondary, and its
    // accents are drawn from the same mark's lilac tail rather than the former
    // indigo, so the two appearances stay one family.

    // Neutrals carry a faint violet cast so surfaces sit under the mark instead
    // of fighting it. They stay close to neutral on purpose — the colour belongs
    // to the mark and the controls, not the page.
    static let desk = dynamic(light: 0xEBE6EE, dark: 0x100C12)
    static let bg = dynamic(light: 0xFDFBFD, dark: 0x18141A)
    static let panel = dynamic(light: 0xF6F1F7, dark: 0x201B23)
    static let elev = dynamic(light: 0xFFFFFF, dark: 0x2A232D)
    static let code = dynamic(light: 0xF8F3F8, dark: 0x141017)

    // Text is violet-ink rather than pure grey, same family as the mark.
    static let text = dynamic(light: 0x241A26, dark: 0xEFE9F1)
    static let text2 = dynamic(light: 0x5E4F63, dark: 0xB2A6B8)
    static let muted = dynamic(light: 0x8E8194, dark: 0x827690)

    /// The contrast-safe ink and icon colour, taken from the mark's deepest
    /// petal tone. Not used as a fill — the petal trio below does that.
    static let accent = dynamic(light: 0x7A1F74, dark: 0xC98FDE)

    // The three-stop petal glass used by primary controls, sampled across the
    // mark's light body: highlight near the mode hue, base in the lilac tail,
    // mid between them.
    static let petalHighlight = dynamic(light: 0xFCD2E7, dark: 0xC98FDE)
    static let petalBase = dynamic(light: 0xE7A9D9, dark: 0xC98FDE)
    static let petalMid = dynamic(light: 0xF2C0E1, dark: 0xC98FDE)

    static let accentEdge = dynamic(light: 0xD79AC9, dark: 0xFFFFFF, lightAlpha: 1, darkAlpha: 0.09)
    /// Foreground on the light petal gradient, so it must be dark, not white.
    static let onAccent = dynamic(light: 0x5E1657, dark: 0x18141A)

    // Status colours deliberately stay outside the mark's 280–339 band. An error
    // that reads as brand pink is an error nobody notices. The light red is
    // darkened from the inherited 0xD1493C, which measured 4.31:1 on `bg` and
    // missed WCAG AA for body text; 0xC63F32 keeps hue 5 and reaches 4.90:1.
    static let green = dynamic(light: 0x1F9E6A, dark: 0x5FD0A0)
    static let red = dynamic(light: 0xC63F32, dark: 0xF0796F)

    static let border = dynamic(light: 0x000000, dark: 0xFFFFFF, lightAlpha: 0.10, darkAlpha: 0.09)
    static let strongBorder = dynamic(light: 0x000000, dark: 0xFFFFFF, lightAlpha: 0.17, darkAlpha: 0.17)
    static let accentWeak = dynamic(light: 0xE7A9D9, dark: 0xC98FDE, lightAlpha: 0.26, darkAlpha: 0.18)
    static let selectionFill = dynamic(light: 0xE7A9D9, dark: 0xC98FDE, lightAlpha: 0.42, darkAlpha: 0.18)
    static let primaryInnerHighlight = dynamic(light: 0xFFFFFF, dark: 0xFFFFFF, lightAlpha: 0.64, darkAlpha: 0)
    static let selectionInnerHighlight = dynamic(light: 0xFFFFFF, dark: 0xFFFFFF, lightAlpha: 0.44, darkAlpha: 0)
    static let warmControlShadow = dynamic(light: 0x7A1F74, dark: 0x000000, lightAlpha: 0.14, darkAlpha: 0)

    static let primaryControlGradient = LinearGradient(
        colors: [petalHighlight, petalBase, petalMid],
        startPoint: .topLeading,
        endPoint: .bottomTrailing
    )

    private static func dynamic(
        light: UInt32,
        dark: UInt32,
        lightAlpha: CGFloat = 1,
        darkAlpha: CGFloat = 1
    ) -> Color {
        Color(NSColor(name: nil) { appearance in
            let isDark = appearance.bestMatch(from: [.darkAqua, .aqua]) == .darkAqua
            let color = isDark ? dark : light
            return NSColor(
                calibratedRed: CGFloat((color >> 16) & 0xFF) / 255,
                green: CGFloat((color >> 8) & 0xFF) / 255,
                blue: CGFloat(color & 0xFF) / 255,
                alpha: isDark ? darkAlpha : lightAlpha
            )
        })
    }
}

/// The macOS 13 fallback for the mark's petal glass. It deliberately
/// avoids a blur or large shadow so primary controls stay crisp and readable.
struct MerlinPrimaryControlSurface: View {
    let cornerRadius: CGFloat

    var body: some View {
        let shape = RoundedRectangle(cornerRadius: cornerRadius, style: .continuous)
        shape
            .fill(MerlinTheme.primaryControlGradient)
            .overlay { shape.stroke(MerlinTheme.accentEdge, lineWidth: 1) }
            .overlay { shape.stroke(MerlinTheme.primaryInnerHighlight, lineWidth: 0.65).padding(0.65) }
            .shadow(color: MerlinTheme.warmControlShadow, radius: 2, y: 1)
    }
}

/// A quieter selected state for navigation and segmented controls. Disabled
/// controls intentionally do not use this surface.
struct MerlinSelectionSurface: View {
    let cornerRadius: CGFloat

    var body: some View {
        let shape = RoundedRectangle(cornerRadius: cornerRadius, style: .continuous)
        shape
            .fill(MerlinTheme.selectionFill)
            .overlay { shape.stroke(MerlinTheme.accentEdge, lineWidth: 1) }
            .overlay { shape.stroke(MerlinTheme.selectionInnerHighlight, lineWidth: 0.6).padding(0.65) }
    }
}

extension View {
    func merlinPanel(cornerRadius: CGFloat = 12, background: Color = MerlinTheme.elev) -> some View {
        self
            .background(background, in: RoundedRectangle(cornerRadius: cornerRadius, style: .continuous))
            .overlay {
                RoundedRectangle(cornerRadius: cornerRadius, style: .continuous)
                    .stroke(MerlinTheme.border, lineWidth: 1)
            }
    }
}

struct ReferenceButtonStyle: ButtonStyle {
    enum Tone { case primary, secondary, quiet }
    let tone: Tone

    func makeBody(configuration: Configuration) -> some View {
        configuration.label
            .font(.system(size: 13, weight: .semibold))
            .foregroundStyle(foreground)
            .padding(.horizontal, 13).padding(.vertical, 8)
            .background { buttonSurface(cornerRadius: 8) }
            .opacity(configuration.isPressed ? 0.78 : 1)
    }

    @ViewBuilder
    private func buttonSurface(cornerRadius: CGFloat) -> some View {
        switch tone {
        case .primary:
            MerlinPrimaryControlSurface(cornerRadius: cornerRadius)
        case .secondary:
            let shape = RoundedRectangle(cornerRadius: cornerRadius, style: .continuous)
            shape.fill(MerlinTheme.elev)
                .overlay { shape.stroke(MerlinTheme.border, lineWidth: 1) }
        case .quiet:
            Color.clear
        }
    }

    private var foreground: Color {
        tone == .primary ? MerlinTheme.onAccent : MerlinTheme.text2
    }
}

struct BrandMark: View {
    @Environment(\.colorScheme) private var colorScheme
    let size: CGFloat

    var body: some View {
        // One liquid-glass flower mark carries both appearances.
        let name = "merlin-flower-liquid-glass"
        Group {
            if let url = brandingURL(named: name),
               let image = NSImage(contentsOf: url) {
                Image(nsImage: image)
                    .resizable()
                    .interpolation(.high)
                    .scaledToFit()
            } else {
                Image(systemName: "wand.and.stars")
                    .resizable()
                    .scaledToFit()
                    .foregroundStyle(MerlinTheme.accent)
            }
        }
        .frame(width: size, height: size)
        .clipShape(RoundedRectangle(cornerRadius: size * 0.24, style: .continuous))
        .accessibilityHidden(true)
    }

    private func brandingURL(named name: String) -> URL? {
        if let bundled = Bundle.main.url(forResource: name, withExtension: "png", subdirectory: "Branding") {
            return bundled
        }
        if let root = ProcessInfo.processInfo.environment["MERLIN_REPO_ROOT"] {
            return URL(fileURLWithPath: root)
                .appendingPathComponent("apps/merlin-macos/AppBundle/Resources/Branding/\(name).png")
        }
        let current = URL(fileURLWithPath: FileManager.default.currentDirectoryPath)
        let candidates = [
            current.appendingPathComponent("AppBundle/Resources/Branding/\(name).png"),
            current.appendingPathComponent("apps/merlin-macos/AppBundle/Resources/Branding/\(name).png"),
            current.deletingLastPathComponent().appendingPathComponent("apps/merlin-macos/AppBundle/Resources/Branding/\(name).png")
        ]
        return candidates.first { FileManager.default.fileExists(atPath: $0.path) }
    }
}

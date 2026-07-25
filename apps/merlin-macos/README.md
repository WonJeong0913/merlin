# Merlin — native macOS harness shell

> **Origin.** Ported on 2026-07-25 from the frozen pre-Merlin workspace at
> `../The king/apps/the-king-macos` (17 files, 5,815 lines). That tree is
> read-only immutable provenance and was not modified. This copy is the active
> Merlin product surface and is the only one that may be changed.
> Renamed identities: `TheKingMac` → `MerlinMac`, `KingTheme` → `MerlinTheme`,
> `KingViewModel` → `MerlinViewModel`, `KingWindow` → `MerlinWindow`,
> `THE_KING_REPO_ROOT` → `MERLIN_REPO_ROOT`, `theKing.*` defaults → `merlin.*`.
> The brand mark is the approved pink/lilac liquid-glass flower, extracted with
> its alpha channel from `Merlin.icns`.

A native SwiftUI, chat-first macOS shell over the local JSONL bridge to the
Python harness `src.merlin_harness`. It is a desktop client for one selected
local workspace — not a web wrapper and not a fixture-driven dashboard.

## Run

macOS 13 is the declared minimum; the exercised path is macOS 14+, Swift 6,
Python 3.11+, and Codex CLI for live chat. No third-party Swift or Python
packages.

```bash
./apps/merlin-macos/scripts/run-app.sh
```

The script builds a Finder-launchable bundle at
`/private/tmp/merlin-macos-build/Merlin.app`, records the repository root for
the Python bridge, ad-hoc signs that temporary local bundle, and opens it.
The ad-hoc signature fixes local LaunchServices verification only; it is not a
developer signature or notarization.

For package-level development:

```bash
cd apps/merlin-macos
swift build
swift test
swift run MerlinMac
```

For an installed bundle set `MERLIN_REPO_ROOT` to the repository root so the
app can locate the local bridge. In Xcode, add the same environment variable to
the executable scheme.

This is a source-distributed developer preview, not a developer-signed or
notarized DMG.

## Layout

| Path | Role |
|---|---|
| `Sources/MerlinMac/App` | `WindowGroup` entry point |
| `Sources/MerlinMac/Domain` | models, slash commands, interface language |
| `Sources/MerlinMac/Features` | theme, window, chat, sidebar, inspector, harness map |
| `Sources/MerlinMac/Services` | JSONL bridge client/protocol, workspace and history stores |
| `AppBundle` | `Info.plist`, `Merlin.icns`, branding mark |
| `scripts/run-app.sh` | build → bundle → launch |

## Self-management surface

The session drawer carries a **Self-management** block backed by the bridge's
`harness.governance` command: campaign standing, the invocation-evidence gate
and its blocking reason, the harness-evolution ledger, and each lifecycle
operation with the reason it is or is not available.

It is read-only by design. Promotion is gated on provider-native invocation
evidence; repair, merge, hide and retirement are evaluator-backed batch
campaigns. A button here would manufacture a decision the harness has not
earned, so the panel reports state and reasons instead.

The decoder keeps absences as absences: a null `g_over_s` stays nil rather than
rendering as a measured `0`, and an absent evolution ledger renders as absent
rather than as a ledger with zero observations.

## Status

- `swift build` passes; `swift test` is 35 tests, 0 failures.
- The bridge peer is `merlin/bridge/merlin_bridge.py`, resolved relative to the
  repository root recorded in the bundle.
- Launch and bundle wiring verified on 2026-07-25. The drawer panel itself was
  not visually confirmed — that needs a live session — but its decoder is
  covered by `Tests/MerlinMacTests/HarnessGovernanceTests.swift` and the bridge
  response was verified end to end over stdio.

> **Origin: The KING (pre-Merlin).** Preserved by the 2026-07-24 migration as a design/idea artifact.
> Results, hashes, run identifiers, and counts in this document are **legacy provenance** and must not be
> relabeled as Merlin evidence. New Merlin claims require new Merlin execution artifacts.
> Source: `The king/docs/live-model-authored-skill-v1.md`

---

# Live Model-Authored Skill Lifecycle v1

## Outcome

The KING now has one live, end-to-end model-authored skill lifecycle rather
than only a registered template compiler.

An account-authenticated Codex CLI run requested `gpt-5.6-terra` with `high`
reasoning effort. It authored the portable three-file skill
`extract-todo-items`. The KING accepted no provider tool use during authoring,
hashed the raw JSONL and exact response, quarantined the bundle, executed it in
fresh macOS-confined workspaces, evaluated visible target and hidden held-out
cases, checked negative routing, and promoted it only into a copy-on-write
provisional library.

Recorded result:

- Visible target exact-file verifier: `0/2 -> 2/2`
- Hidden Korean held-out verifier: `1/1`
- Negative routes: `2/2`
- Promotion gates: `12/12`
- Fresh end-to-end evidence-chain audit: `15/15`
- Lifecycle action: `adopt`
- Live source library mutation: `false`

The provider stream did not report a resolved model ID. The evidence therefore
supports an actual Codex provider run with a requested `gpt-5.6-terra`
contract, not a provider-resolved-model claim.

The retained chain was freshly revalidated again on 2026-07-19 from the raw
authoring JSONL and promoted-chat session. An unrestricted macOS confinement
run reproduced the same audit hash, target `2/2`, hidden `1/1`, negative
`2/2`, and promoted-chat pass. The runner now fails with a distinct
`candidate verifier outcome is unavailable` error when the host cannot apply
`sandbox-exec`; a confinement-runtime failure can no longer be counted as a
candidate verifier failure.

The retained raw authoring trace and promoted-chat session were then audited
again as one byte-bound chain: raw response → three quarantined files →
semantic manifest → fresh isolated target/hidden execution → verified
copy-on-write overlay → negative routes → recorded promoted-script execution
and frozen output verifier. All `15/15` links passed. The packaged report is
hash-only and does not include raw provider text, commands, thread IDs, or
absolute local paths.

## Product Flow

```text
frozen need + visible targets
        ↓
requested-GPT-5.6 strict JSON authoring
        ↓
new-only quarantine + response/raw hashes
        ↓
AST/path/size/secret checks
        ↓
macOS sandbox target + hidden held-out execution
        ↓
negative routing + verifier trust
        ↓
copy-on-write provisional promotion or rejection
        ↓
verified session overlay in the chat agent
```

The chat agent can load this promotion evidence as a session-only overlay. In
the recorded Korean smoke, governed routing exposed only
`extract-todo-items`. The provider trace then read the staged `SKILL.md`, ran
the staged `scripts/run.py --workspace ...` command with exit code zero, and
created the exact two-item `todo-items.json` required by the frozen verifier.

This closes a stronger evidence chain than prompt exposure alone:

| Evidence level | Recorded |
|---|---:|
| Harness selection | yes |
| Prompt exposure | yes |
| Skill body read in provider trace | yes |
| Promoted bundle script execution in provider trace | yes |
| Deterministic task utility | yes |
| Provider-native `Skill` invocation event | no |

The final row remains false because the Codex JSONL surface used here does not
emit a provider-native The KING skill invocation event. The KING does not
upgrade a shell command or model self-report into that stronger claim.

## Reproduction

Run live authoring and promotion with raw artifacts outside the repository:

```bash
PYTHONPATH=. python3 -m experiments.mvp.run_live_model_skill_creation \
  --raw-root /private/tmp/the-king-live-model-skill-v2 \
  --output /private/tmp/the-king-live-model-skill-safe \
  --codex /Applications/ChatGPT.app/Contents/Resources/codex \
  --model gpt-5.6-terra \
  --effort high
```

Load an already verified promotion into the terminal agent:

```bash
PYTHONPATH=. python3 -m experiments.mvp.run_chat \
  --routing-mode deterministic \
  --promotion-evidence experiments/mvp/results/model_authored_skill_live_v1/model_authored_skill_evidence.json
```

Useful terminal commands are `/creation status`, `/creation gates`, and
`/skills`. A fresh default-library session can run the same bounded lifecycle
inside the terminal with:

```text
/learn Extract TODO from backlog.todo into todo-items.json
```

The beta accepts only that verifier-backed TODO contract; unsupported needs
fail before any model call. The overlay loader rechecks the promotion evidence hash, original
and provisional library snapshots, all passed gates, quarantine manifest, and
every candidate file hash before staging the bundle inside the chat session.

`/demo golden` is the compact judge command. It executes the real load,
reference, overload, diagnose, copy-on-write stage, same-verifier verify, and
promote transitions before printing `1/10, 89% shadowing -> 9/10, 0%`
shadowing. It then reviews only a promoted-chat record whose promotion hash
matches the loaded overlay. The command makes no model call and keeps the
controlled recovery and recorded provider turn as distinct evidence lanes.
`/demo recovery` remains the recovery-only command.

## Safe Evidence

- `experiments/mvp/results/model_authored_skill_live_v1/model_authored_skill_evidence.json`
- `experiments/mvp/results/model_authored_skill_live_v1/model_authored_skill_chain_audit.json` (`15/15` hash-only chain audit)
- `experiments/mvp/results/model_authored_skill_live_v1/promoted_chat_smoke.json` (currently hash-bound execution/verification trace used by `/demo golden`)
- `experiments/mvp/results/model_authored_skill_live_v1/promoted_chat_smoke_v2.json` (cleaner historical execution trace; currently rejected because its promotion-evidence byte hash predates the safety-wording-only revision)
- `experiments/mvp/results/model_authored_skill_live_v1/provisional_library.json`
- `experiments/mvp/results/model_authored_skill_live_v1/quarantine/`

The safe evidence contains hashes and normalized observations. Raw provider
JSONL, sandbox profiles, case workspaces, user text, and command output remain
outside the repository.

## Boundaries and Deferred Work

- This is one bounded skill family, not a general generation benchmark.
- The macOS runner is a documented confinement boundary with CPU, file, open
  file, wall-time, read, write, and network controls. It is not claimed to be a
  perfect sandbox for intentionally hostile code.
- General automatic need detection and arbitrary chat-triggered creation are
  not implemented yet; `/learn` is an explicit bounded beta command.
- Two bounded model-authored v1→v2 repair families are now recorded separately
  in `docs/skill-repair-lifecycle-v1.md`; broad repeated repair, merge, and
  retire remain future lifecycle actions.
- Full SkillsBench-87 and repeated statistical evaluation remain post-hackathon
  research work, not part of this product result.

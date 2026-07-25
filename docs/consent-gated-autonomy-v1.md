> **Origin: The KING (pre-Merlin).** Preserved by the 2026-07-24 migration as a design/idea artifact.
> Results, hashes, run identifiers, and counts in this document are **legacy provenance** and must not be
> relabeled as Merlin evidence. New Merlin claims require new Merlin execution artifacts.
> Source: `The king/docs/consent-gated-autonomy-v1.md`

---

# Risk-tiered selective approval v2

Date: 2026-07-20
Classification: architecture, implementation, experiment

## Decision

The KING is not a command-driven skill editor. It detects a capability gap,
chooses a bounded response, and prepares a verification plan by itself. Managed
mode is the default: routine changes may proceed only inside a frozen low-risk,
reversible policy envelope. Human input is required when a proposal needs
elevation beyond that envelope—similar to an operating system asking only when
an action crosses a privilege boundary.

```text
ordinary user request
→ autonomous read-only gap detection
→ dedupe, coverage check, snapshot and bounded plan
→ risk classification and frozen policy-envelope check
→ managed authorization, or explicit permission when elevation is required
→ registered operation compilation and G0–G6 verification
→ copy-on-write session overlay or automatic rollback
→ resume the original request without asking the user to repeat it
```

Managed is the product default, Strict requires permission for every skill
write, and Off is the operator kill switch. A slash command is not consent in
Strict mode. The core governor accepts only an exact natural-language yes/no
reply; ambiguity leaves the proposal pending without a model call or write.

## Authority boundaries

The governor may autonomously inspect active skill metadata, detect a supported
gap, check duplicates/coverage/budget/snapshot, and prepare a hash-only proposal.
Managed authorization is currently allowed only when every condition holds:

- the operation is pre-registered and uses zero provider authoring calls;
- the risk class is `low_reversible_registered_operation`;
- writes are new-only verification evidence plus a COW session overlay;
- the source library remains unchanged and rollback is automatic;
- the session action budget and G0–G6 gates all pass.

Persistent/global changes, open-ended model-authored code, network or paid
model elevation, overwrite/delete/publish, and harness-policy changes require
explicit permission. Strict mode additionally requires permission for the
current low-risk operation. Snapshot drift, duplicate requests, exhausted
budget, malformed consent, verifier failure, or source-isolation failure all
fail closed.

## Implemented v1 slice

`src/the_king/consent_governor.py` supports one registered capability:
converting TODO-prefixed lines in `backlog.todo` to `todo-items.json`. The
detector requires the full input/output contract and a positive extraction
intent. Negative and unrelated requests do not propose a change.

After Managed authorization or Strict approval, the governor compiles only the trusted registered operation,
runs G0 need/snapshot, G1 format, G2 safety, G3 routing, G4 target gain, G5
held-out regression, and G6 COW adoption. It stores request hash/length rather
than raw request or reply. The default session action budget is one. A passing
overlay is installed only in the current session, the source library remains
unchanged, and the original request resumes automatically. The skill change
itself makes zero provider authoring calls; the resumed ordinary chat turn is a
separate, already requested router/executor action and retains its normal model
call contract.

`experiments/mvp/run_chat.py` enables Managed mode by default for authenticated
chat and disables mutation in offline judge mode. `--autonomy-mode strict`
requires permission for every skill change; `consent` remains a compatibility
alias for Strict; `--autonomy-mode off` is the kill switch. `/learn` remains a
separate explicit model-authored beta fallback; it is not silently authorized
by this registered-operation policy.

## Claim boundary

This proves one real Managed auto-authorized path and one Strict
ordinary-chat → permission → verified adoption → resumed-request path. It does
not prove general-purpose risk classification, need detection, open-ended
model-authored generation, autonomous long-horizon lifecycle management, or
production safety across arbitrary tools. Those require more capability
contracts, cost/network permission policies, repeated evaluation, and stronger
invocation evidence.

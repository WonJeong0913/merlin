# Provider-rollout corroboration v2

Date: 2026-07-25
Classification: architecture, evidence

Addresses roadmap P0, "request-bound actual invocation event". It does not
close P0. The original v1 parser was corrected after review because it could
combine body exposure from one turn with an event from another.

## The problem

`skill_body_invocation` emits an HMAC-signed event binding task → selected skill
ID → `SKILL.md` body SHA-256 → model-request hash → execution-trace hash →
verifier result. Every one of those hashes is computed **by the harness, about
the harness**. `model_request_sha256` is the hash of the request Merlin says it
assembled. A harness that fabricated the whole chain would produce an equally
valid signature. The evidence is self-attested, and self-attestation cannot
carry a promotion.

`parse_codex_exec_jsonl` was already explicit that the exec stream does not fix
this: it ignores every tool event for invocation purposes, because a tool call
or a model self-report is not proof that a skill body was loaded. The exec
stream carries `thread.started`, `turn.started`, `item.completed` and reported
model IDs — nothing that echoes the request.

## What was found

The Codex CLI writes a rollout per session under `~/.codex/sessions/YYYY/MM/DD/`.
Measured on this machine, 2026-07-25: 192 rollouts, of which **40 carry
`session_meta.source == "exec"`** — the mode the harness runs. So the mode
Merlin uses does produce these files.

The files provide two useful bindings and one material limitation:

1. `session_meta.id` equals the UUID in the file name and equals the `thread_id`
   the exec stream reports in `thread.started`. The binding is exact.
2. `response_item` / `message` with `role: "user"` records the request text.
3. Merlin does not write the file. A separate process does.
4. **Current observed `turn_context` records do not carry a stable `turn_id`.**
   They cannot identify which user request belongs to a harness event within a
   multi-turn session. V2 refuses to infer that relationship from message order.

That last property is the whole point. Everything else in the chain is Merlin
vouching for Merlin.

## What v2 does

`src/merlin_harness/provider_rollout_evidence.py`:

- `locate_rollout(thread_id)` — finds the one rollout carrying the thread ID.
  Zero matches is an error; **two matches is also an error**. Ambiguity means
  the binding is not established, so nothing is returned rather than guessed.
- `canonical_model_request_sha256(request_text)` — defines the shared request
  contract: UTF-8 bytes from one rollout user message's `input_text` fragments,
  in emitted order, with no wrapper reconstruction or normalization. A harness
  event intended for this check must sign that exact hash.
- `corroborate_skill_body_invocation(event, trusted_signer, thread_id, turn_id,
  skill_body_path)` — first verifies the event against the caller-supplied,
  trusted harness signer. It then requires the filename **and exactly one**
  `session_meta.id` to equal `thread_id`, exactly one `turn_context.turn_id` to
  equal `turn_id`, and exactly one user request in that turn. It rejects the
  check if the canonical request hash differs from
  `event.model_request_sha256`, the body has drifted, or the exact body bytes
  are absent from that one request.

Raw request text never leaves the module. It returns hashes, lengths and
booleans only, because the recorded request contains user content. A test asserts
that a marker string placed in the request does not appear anywhere in the
serialized record.

The returned record keeps `invocation_signature_valid`, `session_bound`,
`turn_bound`, `request_hash_bound`, `skill_body_hash_bound`, and
`skill_body_present_in_recorded_request` separate. `corroborated` is their
logical AND. Ambiguity and mismatch raise an error rather than returning a
partially positive record.

## What it establishes, if v2 returns a record

> A trusted-harness event and one exact CLI-recorded request are bound to the
> same thread and turn; the request hash matches the event; and the exact,
> hash-matched skill body was present in that request.

## What it does not establish

- **Not use.** Presence in a request is not evidence the model read, followed,
  or benefited from the body. Nothing observable from outside establishes that.
- **Not server attestation.** The rollout is written by the *local CLI*, not by
  the provider's service. A compromised CLI or a hand-edited rollout defeats it.
  The record names this residual trust rather than hiding it.

These limits are carried in `evidence_boundary` on every record, and the
governance view ranks all three tiers with their limits, so the weaker claim
cannot be read as the stronger one by a reader who skips the prose.

## Effect on the gate: none, deliberately

`provider_native_evidence_complete` is unchanged and promotion stays blocked.
Tier availability means the *mechanism* exists, not that any observation has
been corroborated. There are still zero matched observations. A test asserts
that an available tier does not flip the promotion gate. The read-only shell
also refuses to show promotion as available: campaign-wide evidence is only a
prerequisite, while a promotion needs the candidate-specific frozen verifier
bundle and gate records.

Closing P0 needs a corroborated observation from a real run, not a mechanism.

## Verification

- `tests/test_provider_rollout_evidence.py` — synthetic provider-contract
  rollouts cover a valid exact binding, forged signature, wrong trusted signer,
  filename/session mismatch, same-session wrong-turn body exposure, request-hash
  mismatch, missing turn ID, malformed lines, body drift, and the privacy
  boundary.
- A prior v1 probe against a real `source: exec` rollout established only that
  its session and user-message record shapes could be read. It is **not v2
  corroboration**: that observed file lacks the stable turn ID v2 requires.

## Next

1. Obtain a provider/CLI rollout schema that emits an immutable stable turn ID
   on both the turn context and the recorded request, or a provider-signed
   request receipt containing that ID and the canonical request hash.
2. Run one real harness turn under that schema and corroborate it end to end.
   This spends account quota and makes a provider call, so it needs the
   operator's go-ahead.
3. Carry the corroboration record into the observation ledger next to the
   signed event, as a distinct named tier — not merged into it.
4. Only then revisit what `actual_invocation_evidence_complete` should require.

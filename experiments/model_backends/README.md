# Model Backend Experiments

This directory holds account-auth and API-auth model backend probes for Merlin.

## Current Backend Status

```text
primary comparison group:
  claude_sonnet5_high = Claude Sonnet 5, effort=high
  claude_opus48_high  = Claude Opus 4.8, effort=high
  claude_opus47_high  = Claude Opus 4.7, effort=high
  codex_gpt55_high    = GPT-5.5 via Codex CLI, effort=high
local Mac Claude Code account backend: connected smoke passed
ONE/WSL Claude Code CLI: installed, auth pending
ONE/WSL Codex CLI: installed, auth pending
```

The canonical machine-readable matrix is `backend-matrix.json`.

After both CLIs are authenticated on ONE/WSL, run the full smoke matrix:

```bash
python3 experiments/model_backends/run_backend_matrix_smoke.py
```

Validated smoke:

```bash
python3 experiments/model_backends/run_cli_smoke.py \
  --backend claude \
  --model claude-sonnet-5 \
  --effort high \
  --run-id claude-sonnet5-high-smoke-20260709-answer-yes

python3 experiments/model_backends/run_cli_smoke.py \
  --backend claude \
  --model claude-opus-4-8 \
  --effort high \
  --run-id claude-opus48-high-smoke-20260709-answer-yes

python3 experiments/model_backends/run_cli_smoke.py \
  --backend claude \
  --model claude-opus-4-7 \
  --effort high \
  --run-id claude-opus47-high-smoke-20260709-answer-yes

python3 experiments/model_backends/run_cli_smoke.py \
  --backend codex \
  --model gpt-5.5 \
  --effort high \
  --run-id codex-gpt55-high-smoke-20260709-answer-yes
```

Result:

```text
task_id=answer-yes
success=true
score=1.0
backend=claude-code
auth_mode=account
usage/cost metadata captured from CLI wrapper
```

## Backend Contract

Each account-auth CLI backend must support:

- non-interactive prompt input
- stable backend/model labels
- JSON output contract: `{"answer": "...", "files": [{"path": "...", "content": "..."}]}`
- raw output, usage, cost, and failure metadata capture
- repeated execution without manual approval inside a trusted benchmark workspace

## OpenAI-compatible low-cost API preflight

The post-Build-Week runtime can now validate and run a bounded task through an
OpenAI-compatible Chat Completions provider. The command accepts an
environment-variable **name**, never a key value:

```bash
export DEEPSEEK_API_KEY="set-locally-never-commit"

PYTHONDONTWRITEBYTECODE=1 python3 experiments/model_backends/run_api_smoke.py \
  --provider deepseek \
  --model deepseek-v4-flash \
  --base-url https://api.deepseek.com \
  --api-key-env DEEPSEEK_API_KEY \
  --input-usd-per-million 0.14 \
  --output-usd-per-million 0.28 \
  --cached-input-usd-per-million 0.0028 \
  --pricing-as-of 2026-07-23 \
  --max-request-cost-usd 0.01 \
  --preflight-only
```

Remove `--preflight-only` only when an actual paid smoke is intended. Results
default to `/private/tmp/merlin-api-smokes/`, not the repository. Refresh the
dated price contract from the provider's official pricing page before a live
campaign.

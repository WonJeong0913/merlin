"""Run one secret-safe OpenAI-compatible API smoke through Merlin.

The command accepts only the *name* of an environment variable containing the
credential.  It never accepts or writes an API key value.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import uuid
from dataclasses import asdict
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from src.merlin_harness.executors import ApiModelConfig, ApiModelExecutor
from src.merlin_harness.provider_runtime import ProviderPricing
from src.merlin_harness.runner import run_task_once
from src.merlin_harness.task_io import load_task


DEFAULT_TASK = Path("experiments/mvp/tasks/answer-yes.json")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run a bounded OpenAI-compatible API backend smoke."
    )
    parser.add_argument("--provider", required=True, help="Stable provider label.")
    parser.add_argument("--model", required=True)
    parser.add_argument("--base-url", required=True)
    parser.add_argument(
        "--api-key-env",
        required=True,
        help="Environment-variable name only; never pass the key value.",
    )
    parser.add_argument("--task", type=Path, default=DEFAULT_TASK)
    parser.add_argument("--run-id")
    parser.add_argument("--output-root", type=Path)
    parser.add_argument("--timeout-s", type=float, default=120.0)
    parser.add_argument("--max-output-tokens", type=int, default=512)
    parser.add_argument("--input-usd-per-million", type=float)
    parser.add_argument("--output-usd-per-million", type=float)
    parser.add_argument("--cached-input-usd-per-million", type=float)
    parser.add_argument("--pricing-as-of")
    parser.add_argument("--max-request-cost-usd", type=float)
    parser.add_argument("--allow-local-http", action="store_true")
    parser.add_argument(
        "--preflight-only",
        action="store_true",
        help="Validate the secret-free contract without reading a key or calling the network.",
    )
    return parser


def _pricing_from_args(args: argparse.Namespace) -> ProviderPricing | None:
    supplied = (
        args.input_usd_per_million,
        args.output_usd_per_million,
        args.cached_input_usd_per_million,
        args.pricing_as_of,
    )
    if not any(value is not None for value in supplied):
        return None
    if args.input_usd_per_million is None or args.output_usd_per_million is None:
        raise ValueError(
            "both --input-usd-per-million and --output-usd-per-million are required "
            "for a pricing contract"
        )
    return ProviderPricing(
        input_usd_per_million=args.input_usd_per_million,
        output_usd_per_million=args.output_usd_per_million,
        cached_input_usd_per_million=args.cached_input_usd_per_million,
        as_of=args.pricing_as_of,
    )


def _safe_contract(
    *,
    args: argparse.Namespace,
    pricing: ProviderPricing | None,
    run_id: str,
) -> dict[str, object]:
    return {
        "schema_version": 1,
        "run_id": run_id,
        "provider": args.provider,
        "model": args.model,
        "protocol": "openai-compatible-chat-completions",
        "base_url": args.base_url,
        "credential_source": f"environment:{args.api_key_env}",
        "credential_stored": False,
        "task": str(args.task),
        "timeout_s": args.timeout_s,
        "max_output_tokens": args.max_output_tokens,
        "pricing": asdict(pricing) if pricing is not None else None,
        "max_request_cost_usd": args.max_request_cost_usd,
        "preflight_only": args.preflight_only,
    }


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        pricing = _pricing_from_args(args)
        run_id = args.run_id or (
            f"{args.provider}-api-smoke-{time.strftime('%Y%m%d-%H%M%S')}-"
            f"{uuid.uuid4().hex[:6]}"
        )
        output_root = (
            args.output_root.expanduser().resolve()
            if args.output_root is not None
            else Path("/private/tmp") / "merlin-api-smokes" / run_id
        )
        output_root.mkdir(parents=True, exist_ok=False)
        contract = _safe_contract(args=args, pricing=pricing, run_id=run_id)
        (output_root / "contract.json").write_text(
            json.dumps(contract, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )

        config = ApiModelConfig(
            model=args.model,
            provider=args.provider,
            base_url=args.base_url,
            protocol="chat_completions",
            api_key_env=args.api_key_env,
            timeout_s=args.timeout_s,
            max_output_tokens=args.max_output_tokens,
            pricing=pricing,
            max_request_cost_usd=args.max_request_cost_usd,
            allow_local_http=args.allow_local_http,
        )
        executor = ApiModelExecutor(
            model=args.model,
            provider=args.provider,
            config=config,
        )
        if args.preflight_only:
            print(json.dumps(contract, ensure_ascii=False, sort_keys=True))
            return 0

        task = load_task(args.task)
        workspace = output_root / "workspace"
        workspace.mkdir()
        trace = run_task_once(
            task=task,
            workspace=workspace,
            condition=f"{args.provider}-api-smoke",
            executor=executor,
        )
        summary = {
            **contract,
            "success": trace.invocation.success if trace.invocation else None,
            "score": trace.invocation.score if trace.invocation else None,
            "trace": asdict(trace),
        }
        (output_root / "summary.json").write_text(
            json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        print(
            json.dumps(
                {
                    key: summary[key]
                    for key in ("run_id", "provider", "model", "success", "score")
                },
                ensure_ascii=False,
                sort_keys=True,
            )
        )
        return 0 if summary["success"] else 1
    except (OSError, RuntimeError, ValueError) as exc:
        parser.error(str(exc))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())

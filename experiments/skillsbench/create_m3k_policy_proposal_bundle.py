"""Create the canonical pre-registered full-87 M3-K proposal bundle."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

from experiments.skillsbench.m3k_policy_proposal import (
    EVIDENCE_FILE_SHA256,
    EVIDENCE_SOURCE_PATH,
    M3KPolicyProposalError,
    build_canonical_bundle,
    validate_canonical_bundle,
)
from src.merlin_harness.management import content_sha256


REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_EVIDENCE = REPO_ROOT / EVIDENCE_SOURCE_PATH


def write_bundle(*, evidence_path: Path, output: Path) -> dict:
    evidence_path = evidence_path.expanduser()
    if evidence_path.is_symlink():
        raise M3KPolicyProposalError("controlled evidence must not be a symlink")
    try:
        evidence_path = evidence_path.resolve(strict=True)
        raw = evidence_path.read_bytes()
        evidence = json.loads(raw)
    except (OSError, json.JSONDecodeError) as exc:
        raise M3KPolicyProposalError("cannot read controlled overload evidence") from exc
    if not evidence_path.is_file() or not isinstance(evidence, dict):
        raise M3KPolicyProposalError("controlled evidence must be a regular JSON object")
    file_sha256 = hashlib.sha256(raw).hexdigest()
    if file_sha256 != EVIDENCE_FILE_SHA256:
        raise M3KPolicyProposalError("controlled evidence file SHA-256 drifted")
    bundle = build_canonical_bundle(evidence, evidence_file_sha256=file_sha256)
    validate_canonical_bundle(bundle)
    output = output.expanduser().resolve(strict=False)
    output.parent.mkdir(parents=True, exist_ok=True)
    encoded = (json.dumps(bundle, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode()
    try:
        with output.open("xb") as handle:
            handle.write(encoded)
    except FileExistsError as exc:
        raise FileExistsError(f"refusing to overwrite M3-K proposal bundle: {output}") from exc
    return bundle


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--evidence", type=Path, default=DEFAULT_EVIDENCE)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    bundle = write_bundle(evidence_path=args.evidence, output=args.output)
    print("Merlin canonical M3-K policy proposal")
    print(f"proposal={bundle['proposal']['id']}")
    print("parent_exposure_budget=10")
    print("candidate_exposure_budget=3")
    print("held_out_used_for_construction=false")
    print("model_execution_performed=false")
    print("benchmark_result_created=false")
    print(f"bundle_semantic_sha256={content_sha256(bundle)}")
    print(f"saved -> {args.output.resolve()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

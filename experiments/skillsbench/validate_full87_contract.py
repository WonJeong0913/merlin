"""Validate the frozen full-87 execution contract without runtime writes."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from experiments.skillsbench.run_full87_c0_c1_batch import sha256_file, validate_manifest


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, required=True)
    args = parser.parse_args(argv)

    manifest = json.loads(args.manifest.read_text(encoding="utf-8"))
    validate_manifest(args.manifest, manifest)
    print("full87_manifest=valid")
    print(f"task_count={len(manifest['task_ids'])}")
    print(f"trial_count={len(manifest['trial_indices'])}")
    print(f"expected_cells={manifest['expected_cells']}")
    print(f"manifest_sha256={sha256_file(args.manifest)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

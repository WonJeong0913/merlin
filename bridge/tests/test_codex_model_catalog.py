from __future__ import annotations

import importlib.util
import json
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path


MODULE_PATH = Path(__file__).resolve().parents[1] / "codex_model_catalog.py"
SPEC = importlib.util.spec_from_file_location("codex_model_catalog", MODULE_PATH)
assert SPEC is not None and SPEC.loader is not None
catalog = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = catalog
SPEC.loader.exec_module(catalog)


def _fake_app_server(directory: Path, models: list[dict[str, object]]) -> Path:
    executable = directory / "fake-codex"
    initialize_response = json.dumps({"id": 1, "result": {"userAgent": "test"}})
    models_response = json.dumps({"id": 2, "result": {"data": models}})
    executable.write_text(
        textwrap.dedent(
            f"""\
            #!/bin/sh
            IFS= read -r initialize_request
            printf '%s\\n' '{initialize_response}'
            IFS= read -r models_request
            printf '%s\\n' '{models_response}'
            """
        ),
        encoding="utf-8",
    )
    executable.chmod(0o700)
    return executable


class CodexModelCatalogTests(unittest.TestCase):
    def test_normalizes_visible_account_models_and_efforts(self) -> None:
        models = [
            {
                "model": "gpt-5.6-sol",
                "displayName": "GPT-5.6-Sol",
                "description": "Frontier model",
                "isDefault": True,
                "defaultReasoningEffort": "low",
                "supportedReasoningEfforts": [
                    {"reasoningEffort": "low", "description": "Fast"},
                    {"reasoningEffort": "ultra", "description": "Deep"},
                ],
            },
            {
                "model": "gpt-5.6-luna",
                "displayName": "GPT-5.6-Luna",
                "description": "Fast model",
                "isDefault": False,
                "defaultReasoningEffort": "medium",
                "supportedReasoningEfforts": [
                    {"reasoningEffort": "medium", "description": "Balanced"}
                ],
            },
        ]
        with tempfile.TemporaryDirectory() as temporary:
            executable = _fake_app_server(Path(temporary), models)
            result = catalog.query_codex_models(executable)

        self.assertEqual(result["default_model"], "gpt-5.6-sol")
        self.assertEqual(
            [item["model"] for item in result["models"]],
            ["gpt-5.6-sol", "gpt-5.6-luna"],
        )
        self.assertEqual(result["models"][0]["supported_efforts"], ["low", "ultra"])

    def test_rejects_catalog_without_valid_selectable_models(self) -> None:
        models = [
            {
                "model": "unsafe model; rm",
                "displayName": "Unsafe",
                "isDefault": True,
                "supportedReasoningEfforts": [],
            }
        ]
        with tempfile.TemporaryDirectory() as temporary:
            executable = _fake_app_server(Path(temporary), models)
            with self.assertRaisesRegex(catalog.CodexModelCatalogError, "no selectable models"):
                catalog.query_codex_models(executable)


if __name__ == "__main__":
    unittest.main()

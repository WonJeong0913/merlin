from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from src.merlin_harness.executors import (
    ApiModelConfig,
    ApiModelExecutor,
    _read_workspace_snapshot,
)
from src.merlin_harness.provider_runtime import (
    OpenAICompatibleChatCompletionsClient,
    OpenAICompatibleProviderConfig,
    ProviderPricing,
    ProviderRuntimeError,
)


class RecordingTransport:
    def __init__(self, response: dict[str, object]) -> None:
        self.response = response
        self.calls: list[dict[str, object]] = []

    def __call__(
        self,
        url: str,
        body: bytes,
        headers: dict[str, str],
        timeout_s: float,
        max_response_bytes: int,
    ) -> bytes:
        self.calls.append(
            {
                "url": url,
                "body": json.loads(body.decode("utf-8")),
                "headers": dict(headers),
                "timeout_s": timeout_s,
                "max_response_bytes": max_response_bytes,
            }
        )
        return json.dumps(self.response).encode("utf-8")


class ProviderRuntimeTests(unittest.TestCase):
    def test_pricing_uses_cached_and_uncached_input_separately(self) -> None:
        pricing = ProviderPricing(
            input_usd_per_million=0.14,
            output_usd_per_million=0.28,
            cached_input_usd_per_million=0.0028,
            as_of="2026-07-23",
        )

        actual = pricing.actual_cost(
            input_tokens=1_000_000,
            cached_input_tokens=250_000,
            output_tokens=100_000,
        )

        self.assertAlmostEqual(actual, 0.1337)
        self.assertGreater(
            pricing.estimate_upper_bound(
                input_chars=40_000,
                max_output_tokens=2_000,
            ),
            0,
        )

    def test_remote_plain_http_and_embedded_credentials_are_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "plain HTTP"):
            OpenAICompatibleProviderConfig(
                provider_id="unsafe",
                model="model",
                base_url="http://example.com/v1",
                api_key_env="UNSAFE_API_KEY",
            )
        with self.assertRaisesRegex(ValueError, "credentials"):
            OpenAICompatibleProviderConfig(
                provider_id="unsafe",
                model="model",
                base_url="https://user:secret@example.com/v1",
                api_key_env="UNSAFE_API_KEY",
            )

    def test_local_http_requires_explicit_opt_in(self) -> None:
        config = OpenAICompatibleProviderConfig(
            provider_id="local",
            model="local-model",
            base_url="http://127.0.0.1:11434/v1",
            api_key_env=None,
            allow_local_http=True,
        )

        self.assertEqual(
            config.chat_completions_url,
            "http://127.0.0.1:11434/v1/chat/completions",
        )

    def test_missing_environment_credential_fails_before_transport(self) -> None:
        transport = RecordingTransport({})
        client = OpenAICompatibleChatCompletionsClient(
            OpenAICompatibleProviderConfig(
                provider_id="deepseek",
                model="deepseek-v4-flash",
                base_url="https://api.deepseek.com",
                api_key_env="MERLIN_TEST_MISSING_KEY",
            ),
            transport=transport,
        )

        with patch.dict(os.environ, {}, clear=True):
            with self.assertRaisesRegex(ProviderRuntimeError, "MERLIN_TEST_MISSING_KEY"):
                client.create_response(
                    {
                        "model": "deepseek-v4-flash",
                        "input": "hello",
                        "max_output_tokens": 16,
                    }
                )

        self.assertEqual(transport.calls, [])

    def test_chat_completion_is_normalized_with_usage_and_cost(self) -> None:
        secret = "unit-test-secret"
        transport = RecordingTransport(
            {
                "id": "response-1",
                "model": "deepseek-v4-flash",
                "choices": [
                    {
                        "message": {
                            "role": "assistant",
                            "content": '{"answer":"yes","files":[]}',
                        }
                    }
                ],
                "usage": {
                    "prompt_tokens": 1_000,
                    "completion_tokens": 100,
                    "prompt_tokens_details": {"cached_tokens": 250},
                },
            }
        )
        pricing = ProviderPricing(
            input_usd_per_million=0.14,
            output_usd_per_million=0.28,
            cached_input_usd_per_million=0.0028,
            as_of="2026-07-23",
        )
        client = OpenAICompatibleChatCompletionsClient(
            OpenAICompatibleProviderConfig(
                provider_id="deepseek",
                model="deepseek-v4-flash",
                base_url="https://api.deepseek.com",
                api_key_env="MERLIN_TEST_DEEPSEEK_KEY",
                pricing=pricing,
                max_request_cost_usd=0.01,
                max_output_tokens=128,
            ),
            transport=transport,
        )

        with patch.dict(
            os.environ,
            {"MERLIN_TEST_DEEPSEEK_KEY": secret},
            clear=True,
        ):
            response = client.create_response(
                {
                    "model": "deepseek-v4-flash",
                    "input": "Return yes as JSON.",
                    "max_output_tokens": 32,
                }
            )

        self.assertEqual(response["output_text"], '{"answer":"yes","files":[]}')
        metadata = response["_merlin_harness_provider"]
        self.assertEqual(metadata["provider_id"], "deepseek")
        self.assertEqual(metadata["usage"]["cached_input_tokens"], 250)
        self.assertGreater(metadata["actual_cost_usd"], 0)
        self.assertFalse(metadata["credential_stored"])
        self.assertNotIn(secret, json.dumps(response))
        self.assertEqual(
            transport.calls[0]["headers"]["Authorization"],
            f"Bearer {secret}",
        )

    def test_cost_ceiling_refuses_request_before_transport(self) -> None:
        transport = RecordingTransport({})
        client = OpenAICompatibleChatCompletionsClient(
            OpenAICompatibleProviderConfig(
                provider_id="expensive",
                model="expensive-model",
                base_url="https://api.example.com/v1",
                api_key_env="MERLIN_TEST_EXPENSIVE_KEY",
                pricing=ProviderPricing(
                    input_usd_per_million=100,
                    output_usd_per_million=100,
                ),
                max_request_cost_usd=0.001,
                max_output_tokens=1_000,
            ),
            transport=transport,
        )

        with patch.dict(
            os.environ,
            {"MERLIN_TEST_EXPENSIVE_KEY": "secret"},
            clear=True,
        ):
            with self.assertRaisesRegex(ProviderRuntimeError, "refused before network"):
                client.create_response(
                    {
                        "model": "expensive-model",
                        "input": "x" * 10_000,
                        "max_output_tokens": 1_000,
                    }
                )

        self.assertEqual(transport.calls, [])

    def test_api_executor_accepts_arbitrary_chat_completions_provider(self) -> None:
        executor = ApiModelExecutor(
            model="deepseek-v4-flash",
            provider="deepseek",
            config=ApiModelConfig(
                model="deepseek-v4-flash",
                provider="deepseek",
                protocol="chat_completions",
                base_url="https://api.deepseek.com",
                api_key_env="DEEPSEEK_API_KEY",
            ),
        )

        self.assertIsInstance(
            executor.client,
            OpenAICompatibleChatCompletionsClient,
        )

    def test_workspace_snapshot_omits_credentials_and_symlink_escape(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            workspace = root / "workspace"
            workspace.mkdir()
            (workspace / "safe.txt").write_text("safe", encoding="utf-8")
            (workspace / ".env").write_text("API_KEY=secret", encoding="utf-8")
            (workspace / "private.pem").write_text("secret", encoding="utf-8")
            (workspace / ".git").mkdir()
            (workspace / ".git" / "config").write_text("secret", encoding="utf-8")
            outside = root / "outside.txt"
            outside.write_text("outside-secret", encoding="utf-8")
            (workspace / "linked.txt").symlink_to(outside)

            snapshot = _read_workspace_snapshot(workspace, 10_000)

        self.assertEqual(snapshot, [{"path": "safe.txt", "content": "safe"}])


if __name__ == "__main__":
    unittest.main()

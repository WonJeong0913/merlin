"""Provider-neutral runtime contracts for OpenAI-compatible model APIs.

The runtime keeps provider credentials out of configuration artifacts and
normalizes chat-completions responses into the narrow response shape consumed
by :class:`ApiModelExecutor`.  It also enforces a conservative pre-call cost
ceiling when the operator supplies a dated pricing contract.
"""

from __future__ import annotations

import json
import math
import os
import re
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import Any, Callable, Mapping, Sequence
from urllib.parse import urlparse


MAX_ERROR_CHARS = 4_000
DEFAULT_MAX_RESPONSE_BYTES = 4 * 1024 * 1024
_ENV_NAME_RE = re.compile(r"^[A-Z][A-Z0-9_]{1,127}$")
_PROVIDER_ID_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}$")
_LOCAL_HOSTS = frozenset({"127.0.0.1", "::1", "localhost"})
_RESERVED_BODY_KEYS = frozenset({"model", "messages", "max_tokens", "stream"})


class ProviderRuntimeError(RuntimeError):
    """Raised when a provider request cannot safely produce trusted output."""


@dataclass(frozen=True, slots=True)
class ProviderPricing:
    """A dated, operator-supplied USD token-price contract."""

    input_usd_per_million: float
    output_usd_per_million: float
    cached_input_usd_per_million: float | None = None
    as_of: str | None = None

    def __post_init__(self) -> None:
        for field_name in (
            "input_usd_per_million",
            "output_usd_per_million",
            "cached_input_usd_per_million",
        ):
            value = getattr(self, field_name)
            if value is not None and (
                isinstance(value, bool) or not math.isfinite(value) or value < 0
            ):
                raise ValueError(f"{field_name} must be a finite non-negative number")
        if self.as_of is not None and not self.as_of.strip():
            raise ValueError("pricing as_of must be non-empty when provided")

    def estimate_upper_bound(
        self,
        *,
        input_chars: int,
        max_output_tokens: int,
        chars_per_token: float = 1.0,
    ) -> float:
        """Estimate a conservative request ceiling before a provider call.

        Exact tokenization is provider-specific.  The estimate deliberately
        ignores cache discounts and rounds the input estimate upward. The
        default assumes one Unicode character per token so CJK input does not
        inherit an English-centric four-characters-per-token underestimate.
        """

        if input_chars < 0 or max_output_tokens < 0 or chars_per_token <= 0:
            raise ValueError("token-estimation inputs must be non-negative")
        estimated_input_tokens = math.ceil(input_chars / chars_per_token)
        return (
            estimated_input_tokens * self.input_usd_per_million
            + max_output_tokens * self.output_usd_per_million
        ) / 1_000_000

    def actual_cost(
        self,
        *,
        input_tokens: int,
        output_tokens: int,
        cached_input_tokens: int = 0,
    ) -> float:
        if min(input_tokens, output_tokens, cached_input_tokens) < 0:
            raise ValueError("usage tokens must be non-negative")
        cached = min(cached_input_tokens, input_tokens)
        uncached = input_tokens - cached
        cached_rate = (
            self.cached_input_usd_per_million
            if self.cached_input_usd_per_million is not None
            else self.input_usd_per_million
        )
        return (
            uncached * self.input_usd_per_million
            + cached * cached_rate
            + output_tokens * self.output_usd_per_million
        ) / 1_000_000


@dataclass(frozen=True, slots=True)
class NormalizedUsage:
    input_tokens: int
    output_tokens: int
    cached_input_tokens: int = 0

    @classmethod
    def from_payload(cls, payload: Mapping[str, Any] | None) -> "NormalizedUsage | None":
        if not isinstance(payload, Mapping):
            return None

        input_tokens = payload.get("prompt_tokens", payload.get("input_tokens"))
        output_tokens = payload.get("completion_tokens", payload.get("output_tokens"))
        if (
            isinstance(input_tokens, bool)
            or not isinstance(input_tokens, int)
            or isinstance(output_tokens, bool)
            or not isinstance(output_tokens, int)
            or input_tokens < 0
            or output_tokens < 0
        ):
            return None

        details = payload.get(
            "prompt_tokens_details",
            payload.get("input_tokens_details", {}),
        )
        cached = details.get("cached_tokens", 0) if isinstance(details, Mapping) else 0
        if isinstance(cached, bool) or not isinstance(cached, int) or cached < 0:
            cached = 0
        return cls(
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cached_input_tokens=min(cached, input_tokens),
        )

    def to_dict(self) -> dict[str, int]:
        return {
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "cached_input_tokens": self.cached_input_tokens,
        }


@dataclass(frozen=True, slots=True)
class OpenAICompatibleProviderConfig:
    """Secret-free configuration for one OpenAI-compatible chat endpoint."""

    provider_id: str
    model: str
    base_url: str
    api_key_env: str | None
    timeout_s: float = 120.0
    max_output_tokens: int = 2_048
    max_response_bytes: int = DEFAULT_MAX_RESPONSE_BYTES
    pricing: ProviderPricing | None = None
    max_request_cost_usd: float | None = None
    allow_local_http: bool = False
    extra_headers: Mapping[str, str] = field(default_factory=dict)
    extra_body: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not _PROVIDER_ID_RE.fullmatch(self.provider_id):
            raise ValueError("provider_id must use lowercase letters, numbers, dot, dash, or underscore")
        if not isinstance(self.model, str) or not self.model.strip():
            raise ValueError("provider model must be non-empty")
        if self.api_key_env is not None and not _ENV_NAME_RE.fullmatch(self.api_key_env):
            raise ValueError("api_key_env must be an uppercase environment-variable name")
        if self.timeout_s <= 0:
            raise ValueError("provider timeout_s must be positive")
        if isinstance(self.max_output_tokens, bool) or not 1 <= self.max_output_tokens <= 1_000_000:
            raise ValueError("max_output_tokens must be from 1 through 1,000,000")
        if (
            isinstance(self.max_response_bytes, bool)
            or not 1_024 <= self.max_response_bytes <= 64 * 1024 * 1024
        ):
            raise ValueError("max_response_bytes must be from 1 KiB through 64 MiB")
        if self.max_request_cost_usd is not None and (
            isinstance(self.max_request_cost_usd, bool)
            or not math.isfinite(self.max_request_cost_usd)
            or self.max_request_cost_usd <= 0
        ):
            raise ValueError("max_request_cost_usd must be a finite positive number")
        if self.max_request_cost_usd is not None and self.pricing is None:
            raise ValueError("a pricing contract is required when max_request_cost_usd is set")

        parsed = urlparse(self.base_url)
        if parsed.username or parsed.password or parsed.query or parsed.fragment:
            raise ValueError("provider base_url must not contain credentials, query, or fragment")
        if parsed.scheme not in {"https", "http"} or not parsed.hostname:
            raise ValueError("provider base_url must be an absolute HTTP(S) URL")
        if parsed.scheme == "http" and (
            not self.allow_local_http or parsed.hostname.lower() not in _LOCAL_HOSTS
        ):
            raise ValueError("plain HTTP is allowed only for an explicitly enabled local endpoint")
        for key, value in self.extra_headers.items():
            if not isinstance(key, str) or not key.strip() or not isinstance(value, str):
                raise ValueError("extra_headers must contain non-empty string keys and string values")
            if key.lower() in {"authorization", "proxy-authorization"}:
                raise ValueError("authorization headers must come from api_key_env, not configuration")
            if key.lower() in {"content-length", "host"}:
                raise ValueError("transport-owned headers cannot be overridden")
        overridden = sorted(_RESERVED_BODY_KEYS.intersection(self.extra_body))
        if overridden:
            raise ValueError(
                "extra_body cannot override frozen request fields: "
                + ", ".join(overridden)
            )

    @property
    def chat_completions_url(self) -> str:
        base = self.base_url.rstrip("/")
        if base.endswith("/chat/completions"):
            return base
        return f"{base}/chat/completions"


Transport = Callable[[str, bytes, Mapping[str, str], float, int], bytes]


def _default_transport(
    url: str,
    body: bytes,
    headers: Mapping[str, str],
    timeout_s: float,
    max_response_bytes: int,
) -> bytes:
    request = urllib.request.Request(
        url,
        data=body,
        headers=dict(headers),
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=timeout_s) as response:
        data = response.read(max_response_bytes + 1)
    if len(data) > max_response_bytes:
        raise ProviderRuntimeError("provider response exceeded the configured byte limit")
    return data


def _redact(value: str, secrets: Sequence[str]) -> str:
    redacted = value
    for secret in secrets:
        if secret:
            redacted = redacted.replace(secret, "<credential-redacted>")
    if len(redacted) > MAX_ERROR_CHARS:
        redacted = redacted[:MAX_ERROR_CHARS] + "\n[provider diagnostic truncated]"
    return redacted


def _extract_chat_text(payload: Mapping[str, Any]) -> str:
    choices = payload.get("choices")
    if not isinstance(choices, list) or not choices or not isinstance(choices[0], Mapping):
        raise ProviderRuntimeError("provider response contains no chat completion choice")
    message = choices[0].get("message")
    if not isinstance(message, Mapping):
        raise ProviderRuntimeError("provider response contains no assistant message")
    content = message.get("content")
    if isinstance(content, str):
        return content.strip()
    if isinstance(content, list):
        parts: list[str] = []
        for item in content:
            if isinstance(item, Mapping) and isinstance(item.get("text"), str):
                parts.append(item["text"])
        if parts:
            return "\n".join(parts).strip()
    raise ProviderRuntimeError("provider assistant message contains no text")


class OpenAICompatibleChatCompletionsClient:
    """Normalize an OpenAI-compatible chat completion without storing its key."""

    def __init__(
        self,
        config: OpenAICompatibleProviderConfig,
        *,
        transport: Transport = _default_transport,
    ) -> None:
        self.config = config
        self._transport = transport

    def create_response(self, payload: dict[str, Any]) -> dict[str, Any]:
        requested_model = payload.get("model")
        if requested_model != self.config.model:
            raise ProviderRuntimeError(
                "request model does not match the frozen provider configuration"
            )
        prompt = payload.get("input")
        if not isinstance(prompt, str) or not prompt.strip():
            raise ProviderRuntimeError("provider request input must be non-empty text")
        requested_max = payload.get("max_output_tokens", self.config.max_output_tokens)
        if (
            isinstance(requested_max, bool)
            or not isinstance(requested_max, int)
            or not 1 <= requested_max <= self.config.max_output_tokens
        ):
            raise ProviderRuntimeError("request max_output_tokens exceeds the provider contract")

        estimated_cost: float | None = None
        if self.config.pricing is not None:
            estimated_cost = self.config.pricing.estimate_upper_bound(
                input_chars=len(prompt),
                max_output_tokens=requested_max,
            )
            if (
                self.config.max_request_cost_usd is not None
                and estimated_cost > self.config.max_request_cost_usd
            ):
                raise ProviderRuntimeError(
                    "provider request refused before network access: estimated upper-bound cost "
                    f"${estimated_cost:.6f} exceeds ${self.config.max_request_cost_usd:.6f}"
                )

        api_key = (
            os.environ.get(self.config.api_key_env, "")
            if self.config.api_key_env is not None
            else ""
        )
        if self.config.api_key_env is not None and not api_key:
            raise ProviderRuntimeError(
                f"provider credential is missing from environment variable {self.config.api_key_env}"
            )

        request_body: dict[str, Any] = {
            "model": self.config.model,
            "messages": [{"role": "user", "content": prompt}],
            "max_tokens": requested_max,
            "stream": False,
        }
        request_body.update(dict(self.config.extra_body))
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json",
            **dict(self.config.extra_headers),
        }
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"

        try:
            raw = self._transport(
                self.config.chat_completions_url,
                json.dumps(request_body, ensure_ascii=False).encode("utf-8"),
                headers,
                self.config.timeout_s,
                self.config.max_response_bytes,
            )
        except urllib.error.HTTPError as exc:
            detail = exc.read(self.config.max_response_bytes).decode(
                "utf-8", errors="replace"
            )
            raise ProviderRuntimeError(
                f"provider request failed with HTTP {exc.code}: {_redact(detail, [api_key])}"
            ) from exc
        except urllib.error.URLError as exc:
            raise ProviderRuntimeError(
                f"provider connection failed: {_redact(str(exc.reason), [api_key])}"
            ) from exc
        except TimeoutError as exc:
            raise ProviderRuntimeError("provider request timed out") from exc

        if len(raw) > self.config.max_response_bytes:
            raise ProviderRuntimeError("provider response exceeded the configured byte limit")
        try:
            decoded = json.loads(raw.decode("utf-8"))
        except (UnicodeError, json.JSONDecodeError) as exc:
            raise ProviderRuntimeError("provider returned invalid JSON") from exc
        if not isinstance(decoded, dict):
            raise ProviderRuntimeError("provider response must be a JSON object")

        text = _extract_chat_text(decoded)
        usage = NormalizedUsage.from_payload(decoded.get("usage"))
        actual_cost = (
            self.config.pricing.actual_cost(
                input_tokens=usage.input_tokens,
                output_tokens=usage.output_tokens,
                cached_input_tokens=usage.cached_input_tokens,
            )
            if self.config.pricing is not None and usage is not None
            else None
        )
        provider_metadata = {
            "provider_id": self.config.provider_id,
            "protocol": "openai-compatible-chat-completions",
            "requested_model": self.config.model,
            "reported_model": (
                decoded.get("model") if isinstance(decoded.get("model"), str) else None
            ),
            "credential_source": (
                f"environment:{self.config.api_key_env}"
                if self.config.api_key_env is not None
                else "none-local-endpoint"
            ),
            "credential_stored": False,
            "estimated_upper_bound_cost_usd": estimated_cost,
            "actual_cost_usd": actual_cost,
            "pricing_as_of": self.config.pricing.as_of if self.config.pricing else None,
            "usage": usage.to_dict() if usage is not None else None,
        }
        return {
            "id": decoded.get("id"),
            "model": decoded.get("model"),
            "output_text": text,
            "usage": decoded.get("usage"),
            "_merlin_harness_provider": provider_metadata,
        }

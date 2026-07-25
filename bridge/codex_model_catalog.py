"""Bounded Codex app-server model catalog discovery.

The catalog is account/runtime data, not an application allowlist.  The app
server decides which models are visible for the authenticated Codex account.
Only non-sensitive picker metadata crosses the desktop bridge.
"""

from __future__ import annotations

import json
import re
import selectors
import subprocess
import time
from pathlib import Path
from typing import Any


MAX_STDOUT_BYTES = 2 * 1024 * 1024
MAX_MODELS = 100
MAX_TEXT_CHARS = 512
MODEL_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
EFFORT_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,31}$")


class CodexModelCatalogError(RuntimeError):
    """Safe failure raised when the account model catalog is unavailable."""


def _bounded_text(value: Any, *, required: bool = False) -> str | None:
    if not isinstance(value, str):
        if required:
            raise CodexModelCatalogError("Codex model catalog returned invalid text")
        return None
    text = " ".join(value.split())
    if required and not text:
        raise CodexModelCatalogError("Codex model catalog returned empty text")
    if not text:
        return None
    return text[:MAX_TEXT_CHARS]


def _normalize_model(value: Any) -> dict[str, Any] | None:
    if not isinstance(value, dict):
        return None
    model = value.get("model")
    if not isinstance(model, str) or not MODEL_RE.fullmatch(model):
        return None
    display_name = _bounded_text(value.get("displayName")) or model
    description = _bounded_text(value.get("description")) or ""
    raw_efforts = value.get("supportedReasoningEfforts", [])
    efforts: list[str] = []
    if isinstance(raw_efforts, list):
        for item in raw_efforts:
            if not isinstance(item, dict):
                continue
            effort = item.get("reasoningEffort")
            if isinstance(effort, str) and EFFORT_RE.fullmatch(effort) and effort not in efforts:
                efforts.append(effort)
    default_effort = value.get("defaultReasoningEffort")
    if not isinstance(default_effort, str) or default_effort not in efforts:
        default_effort = efforts[0] if efforts else None
    return {
        "model": model,
        "display_name": display_name,
        "description": description,
        "is_default": value.get("isDefault") is True,
        "default_effort": default_effort,
        "supported_efforts": efforts,
    }


def query_codex_models(
    executable: str | Path,
    *,
    timeout_s: float = 15.0,
) -> dict[str, Any]:
    """Return the authenticated runtime's visible model picker catalog.

    A short-lived stdio app-server receives initialize and model/list requests.
    Closing stdin after the requests lets the subprocess exit without leaving a
    daemon behind.  Notifications and unrelated JSONL records are ignored.
    """

    executable_path = Path(executable).expanduser().resolve()
    if not executable_path.is_file():
        raise CodexModelCatalogError("Codex CLI executable is unavailable")
    initialize = {
        "id": 1,
        "method": "initialize",
        "params": {
            "clientInfo": {
                "name": "merlin",
                "title": "Merlin",
                "version": "0.1",
            },
            "capabilities": {"experimentalApi": True},
        },
    }
    model_list = {
        "id": 2,
        "method": "model/list",
        "params": {"limit": MAX_MODELS, "includeHidden": False},
    }
    process: subprocess.Popen[str] | None = None
    selector: selectors.BaseSelector | None = None
    try:
        process = subprocess.Popen(
            [str(executable_path), "app-server", "--listen", "stdio://"],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            bufsize=1,
        )
        if process.stdin is None or process.stdout is None:
            raise CodexModelCatalogError("Codex model catalog pipes are unavailable")
        selector = selectors.DefaultSelector()
        selector.register(process.stdout, selectors.EVENT_READ)
        deadline = time.monotonic() + timeout_s
        total_bytes = 0

        def exchange(request: dict[str, Any], response_id: int) -> dict[str, Any]:
            nonlocal total_bytes
            assert process is not None and process.stdin is not None and process.stdout is not None
            process.stdin.write(json.dumps(request, separators=(",", ":")) + "\n")
            process.stdin.flush()
            while True:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise CodexModelCatalogError("Codex model catalog request timed out")
                assert selector is not None
                if not selector.select(remaining):
                    raise CodexModelCatalogError("Codex model catalog request timed out")
                line = process.stdout.readline()
                if not line:
                    raise CodexModelCatalogError("Codex model catalog process ended early")
                total_bytes += len(line.encode("utf-8", errors="replace"))
                if total_bytes > MAX_STDOUT_BYTES:
                    raise CodexModelCatalogError(
                        "Codex model catalog response exceeded its size limit"
                    )
                try:
                    message = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if not isinstance(message, dict) or message.get("id") != response_id:
                    continue
                if "error" in message:
                    raise CodexModelCatalogError("Codex rejected the model catalog request")
                return message

        exchange(initialize, 1)
        message = exchange(model_list, 2)
    except (OSError, BrokenPipeError) as exc:
        raise CodexModelCatalogError("Codex model catalog request failed safely") from exc
    finally:
        if selector is not None:
            selector.close()
        if process is not None:
            if process.stdin is not None:
                process.stdin.close()
            if process.stdout is not None:
                process.stdout.close()
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=2)

    result = message.get("result")
    if not isinstance(result, dict):
        raise CodexModelCatalogError("Codex returned no model catalog response")

    raw_models = result.get("data")
    if not isinstance(raw_models, list):
        raise CodexModelCatalogError("Codex returned an invalid model catalog")
    models = [model for value in raw_models[:MAX_MODELS] if (model := _normalize_model(value))]
    if not models:
        raise CodexModelCatalogError("Codex returned no selectable models")
    default_model = next((item["model"] for item in models if item["is_default"]), None)
    return {
        "source": "codex_app_server_model_list",
        "models": models,
        "default_model": default_model,
    }

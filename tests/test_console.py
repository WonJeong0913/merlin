from __future__ import annotations

import http.client
import json
import threading
import unittest
from pathlib import Path
from typing import Any
from unittest.mock import patch

from experiments.mvp.console import (
    ConsoleHTTPServer,
    MAX_JSON_BODY_BYTES,
    create_console_server,
    merlin_product_status,
)


class MerlinProductStatusTests(unittest.TestCase):
    def test_campaign_status_is_loaded_from_the_validated_merlin_artifact(self) -> None:
        status = merlin_product_status()

        self.assertTrue(status["campaign_valid"])
        self.assertEqual(status["task_count"], 50)
        self.assertEqual(status["pair_count"], 100)
        self.assertEqual(status["observation_count"], 0)
        self.assertEqual(status["level_7_status"], "not-yet-qualified")
        self.assertFalse(status["invocation_evidence_complete"])


class ConsoleHTTPTests(unittest.TestCase):
    def setUp(self) -> None:
        self.server = create_console_server(port=0)
        self.port = self.server.server_address[1]
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self) -> None:
        self.server.shutdown()
        self.thread.join(timeout=3)
        self.server.server_close()

    def request(
        self,
        method: str,
        path: str,
        payload: dict[str, Any] | bytes | None = None,
        *,
        token: str | None = "valid",
        origin: str | None = "valid",
        content_type: str | None = "application/json",
        host: str | None = None,
        extra_headers: dict[str, str] | None = None,
    ) -> tuple[int, dict[str, str], bytes]:
        connection = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        if isinstance(payload, dict):
            body = json.dumps(payload).encode("utf-8")
        else:
            body = payload
        headers: dict[str, str] = {}
        if host is not None:
            headers["Host"] = host
        if token is not None:
            headers["X-Merlin-Token"] = self.server.csrf_token if token == "valid" else token
        if origin is not None:
            headers["Origin"] = self.server.base_url if origin == "valid" else origin
        if content_type is not None:
            headers["Content-Type"] = content_type
        if extra_headers:
            headers.update(extra_headers)
        connection.request(method, path, body=body, headers=headers)
        response = connection.getresponse()
        data = response.read()
        result = response.status, {key.lower(): value for key, value in response.getheaders()}, data
        connection.close()
        return result

    def post_action(self, action: str, **fields: Any) -> dict[str, Any]:
        status, _headers, body = self.request("POST", "/api/action", {"action": action, **fields})
        self.assertEqual(status, 200, body.decode("utf-8"))
        return json.loads(body)["state"]

    def test_ephemeral_http_flow_executes_each_runtime_step_and_exports_report(self) -> None:
        self.assertGreater(self.port, 0)
        status, headers, body = self.request("GET", "/", token=None, origin=None, content_type=None)
        self.assertEqual(status, 200)
        self.assertEqual(headers["x-frame-options"], "DENY")
        self.assertIn(b"Merlin Console", body)

        status, _headers, body = self.request("GET", "/api/merlin-status", token=None, origin=None, content_type=None)
        self.assertEqual(status, 200)
        campaign = json.loads(body)
        self.assertTrue(campaign["campaign_valid"])
        self.assertEqual(campaign["task_count"], 50)
        self.assertEqual(campaign["pair_count"], 100)
        self.assertEqual(campaign["observation_count"], 0)
        self.assertFalse(campaign["invocation_evidence_complete"])

        status, headers, body = self.request("GET", "/assets/merlin-flower-liquid-glass.png", token=None, origin=None, content_type=None)
        self.assertEqual(status, 200)
        self.assertEqual(headers["content-type"], "image/png")
        self.assertTrue(body.startswith(b"\x89PNG"))

        self.post_action("load_sample", min_shadowing_events=2)
        self.post_action("run_reference")
        self.post_action("run_overloaded")
        diagnosed = self.post_action("diagnose")
        self.assertEqual(len(diagnosed["decisions"]), 2)
        self.post_action("stage_hide")
        verified = self.post_action("verify_and_promote")
        self.assertEqual(verified["stage"], "verified")
        self.assertEqual(verified["metrics"]["reference"]["passed"], 9)
        self.assertEqual(verified["metrics"]["overloaded"]["passed"], 1)
        self.assertEqual(verified["metrics"]["provisional"]["passed"], 9)
        self.assertEqual(verified["metrics"]["overloaded"]["pi_m"], 8 / 9)
        self.assertEqual(verified["metrics"]["provisional"]["pi_m"], 0.0)

        status, headers, body = self.request("GET", "/download/report.json", token=None, origin=None, content_type=None)
        self.assertEqual(status, 200)
        self.assertIn("attachment", headers["content-disposition"])
        report = json.loads(body)
        self.assertEqual(report["schema_version"], 2)
        self.assertTrue(report["promotion"]["accepted"])
        status, _headers, body = self.request("GET", "/report", token=None, origin=None, content_type=None)
        self.assertEqual(status, 200)
        self.assertIn(b"Merlin Control Room", body)

    def test_illegal_transition_returns_409_and_preserves_state(self) -> None:
        status, _headers, body = self.request("POST", "/api/action", {"action": "diagnose"})
        self.assertEqual(status, 409)
        self.assertEqual(json.loads(body)["error"]["code"], "invalid_transition")
        status, _headers, body = self.request("GET", "/api/state", token=None, origin=None, content_type=None)
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body)["state"]["stage"], "empty")

        status, _headers, body = self.request("GET", "/api/report", token=None, origin=None, content_type=None)
        self.assertEqual(status, 409)
        self.assertIn("pending", json.loads(body)["error"]["message"])

    def test_host_origin_and_csrf_token_are_enforced(self) -> None:
        status, _headers, body = self.request(
            "GET", "/api/state", token=None, origin=None, content_type=None, host="localhost"
        )
        self.assertEqual(status, 400)
        self.assertEqual(json.loads(body)["error"]["code"], "invalid_host")

        status, _headers, body = self.request(
            "POST", "/api/action", {"action": "reset"}, origin="https://attacker.invalid"
        )
        self.assertEqual(status, 403)
        self.assertEqual(json.loads(body)["error"]["code"], "invalid_origin")

        status, _headers, body = self.request(
            "POST", "/api/action", {"action": "reset"}, token="wrong-token"
        )
        self.assertEqual(status, 403)
        self.assertEqual(json.loads(body)["error"]["code"], "invalid_token")

    def test_json_contract_rejects_content_type_size_shape_and_extra_fields(self) -> None:
        cases = (
            ({"action": "reset"}, "application/json; charset=utf-8", 415, "invalid_content_type"),
            (b"{broken", "application/json", 400, "invalid_json"),
            ({"action": 7}, "application/json", 400, "invalid_action"),
            ({"action": "reset", "path": "/tmp"}, "application/json", 400, "unexpected_fields"),
            ({"action": "load_sample"}, "application/json", 400, "threshold_required"),
            ({"action": "load_sample", "min_shadowing_events": 9}, "application/json", 400, "invalid_threshold"),
            ({"action": "delete_everything"}, "application/json", 400, "unknown_action"),
        )
        for payload, content_type, expected_status, code in cases:
            with self.subTest(code=code):
                status, _headers, body = self.request(
                    "POST", "/api/action", payload, content_type=content_type
                )
                self.assertEqual(status, expected_status)
                self.assertEqual(json.loads(body)["error"]["code"], code)

        status, _headers, body = self.request(
            "POST",
            "/api/action",
            b"x",
            extra_headers={"Content-Length": str(MAX_JSON_BODY_BYTES + 1)},
        )
        self.assertEqual(status, 413)
        self.assertEqual(json.loads(body)["error"]["code"], "body_too_large")

    def test_method_and_path_allowlist_has_no_shutdown_or_arbitrary_path_surface(self) -> None:
        for method in ("PUT", "PATCH", "DELETE", "OPTIONS", "HEAD"):
            with self.subTest(method=method):
                status, _headers, _body = self.request(method, "/api/action", payload=None)
                self.assertEqual(status, 405)
        for path in ("/shutdown", "/api/files", "/../private", "/favicon.ico"):
            with self.subTest(path=path):
                status, _headers, _body = self.request("GET", path, token=None, origin=None, content_type=None)
                self.assertEqual(status, 404)

    def test_console_html_is_self_contained_public_and_accessible_by_contract(self) -> None:
        status, headers, body = self.request("GET", "/", token=None, origin=None, content_type=None)
        document = body.decode("utf-8")
        self.assertEqual(status, 200)
        self.assertIn("default-src 'none'", headers["content-security-policy"])
        self.assertIn('aria-live="polite"', document)
        self.assertIn('role="alert"', document)
        self.assertIn('src="/assets/merlin-flower-liquid-glass.png"', document)
        self.assertIn("Current evidence boundary", document)
        self.assertIn(':focus-visible', document)
        self.assertIn('@media (max-width: 620px)', document)
        self.assertNotIn("https://", document)
        self.assertNotIn("http://", document)
        self.assertNotIn("/Users/", document)
        self.assertNotIn("/private/", document)
        self.assertNotIn("<script src=", document)

    def test_concurrent_duplicate_action_serializes_and_one_request_gets_409(self) -> None:
        self.post_action("load_sample", min_shadowing_events=2)
        barrier = threading.Barrier(3)
        results: list[int] = []

        def run_reference() -> None:
            barrier.wait()
            status, _headers, _body = self.request(
                "POST", "/api/action", {"action": "run_reference"}
            )
            results.append(status)

        workers = [threading.Thread(target=run_reference) for _ in range(2)]
        for worker in workers:
            worker.start()
        barrier.wait()
        for worker in workers:
            worker.join(timeout=5)

        self.assertCountEqual(results, [200, 409])
        self.assertEqual(self.server.session.stage.value, "reference_complete")

    def test_server_close_cleans_loaded_temporary_workspace(self) -> None:
        self.post_action("load_sample", min_shadowing_events=2)
        temporary = self.server.session._temporary
        self.assertIsNotNone(temporary)
        path = Path(temporary.name)  # type: ignore[union-attr]
        self.assertTrue(path.is_dir())

        self.server.shutdown()
        self.thread.join(timeout=3)
        self.server.server_close()
        self.assertFalse(path.exists())
        self.assertIsNone(self.server.session._temporary)


class ConsoleFactoryTests(unittest.TestCase):
    def test_factory_rejects_non_loopback_host_and_invalid_port(self) -> None:
        with self.assertRaisesRegex(ValueError, "127.0.0.1"):
            create_console_server(host="0.0.0.0")
        for port in (-1, 65536, True, "0"):
            with self.subTest(port=port):
                with self.assertRaises(ValueError):
                    create_console_server(port=port)  # type: ignore[arg-type]

    def test_bind_failure_cleans_partially_constructed_server_without_attribute_error(self) -> None:
        with patch.object(ConsoleHTTPServer, "server_bind", side_effect=PermissionError("denied")):
            with self.assertRaisesRegex(PermissionError, "denied"):
                create_console_server(port=0)


if __name__ == "__main__":
    unittest.main()

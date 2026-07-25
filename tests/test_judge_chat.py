from __future__ import annotations

import http.client
import json
import threading
import unittest
from typing import Any

from experiments.mvp.judge_chat import (
    MAX_JSON_BODY_BYTES,
    create_judge_chat_server,
)
from experiments.mvp.route_trace_audit import sample_trace_bundle


class JudgeChatHTTPTests(unittest.TestCase):
    def setUp(self) -> None:
        self.server = create_judge_chat_server(port=0)
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
        connection = http.client.HTTPConnection("127.0.0.1", self.port, timeout=8)
        body = json.dumps(payload).encode() if isinstance(payload, dict) else payload
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

    def post(self, path: str, payload: dict[str, Any]) -> dict[str, Any]:
        status, _headers, body = self.request("POST", path, payload)
        self.assertEqual(status, 200, body.decode())
        return json.loads(body)["state"]

    def test_one_chat_request_runs_real_lifecycle_and_unlocks_evidence(self) -> None:
        state = self.post(
            "/api/message",
            {"message": "Diagnose and safely recover this overloaded skill library."},
        )
        self.assertEqual(state["status"], "complete")
        self.assertEqual(state["metrics"]["before_pass"], "1/10")
        self.assertEqual(state["metrics"]["after_pass"], "9/10")
        self.assertEqual(state["metrics"]["promotion_gates"], "12/12 gates")
        self.assertEqual(state["metrics"]["evidence_chain"], "15/15 chain")
        self.assertEqual(state["metrics"]["rollback_audit"], "9/9 rollback")
        self.assertEqual(
            state["metrics"]["selection_pilot"], "11/12 @56 · selection only"
        )
        self.assertEqual(
            state["metrics"]["name_collision_guard"],
            "3 suppressed · model-free",
        )
        result = state["messages"][-1]
        self.assertEqual(result["kind"], "incident_result")
        self.assertEqual([item["tool"] for item in result["tools"]], [
            "inspect_library",
            "diagnose_routes",
            "stage_copy_on_write",
            "verify_and_promote",
            "review_recorded_creation",
        ])
        self.assertEqual(
            [item["lane_kind"] for item in result["tools"]],
            ["runtime", "runtime", "runtime", "runtime", "recorded"],
        )
        self.assertTrue(
            all(item["lane"] == "CONTROLLED RUNTIME · RUN NOW" for item in result["tools"][:4])
        )
        self.assertEqual(result["tools"][4]["lane"], "RECORDED GPT-5.6 EVIDENCE")
        self.assertIn("passed target 2/2 and hidden 1/1", result["tools"][4]["summary"])
        self.assertIn("rolled back on route shadowing", result["tools"][4]["summary"])
        self.assertIn("selection-only 6/16/56/209 pilot", result["tools"][4]["summary"])
        self.assertIn("non-monotonic exact-variant mismatch", result["tools"][4]["summary"])
        self.assertIn("56 variants/53 names", result["tools"][4]["summary"])
        self.assertIn("suppressed 3 variants", result["tools"][4]["summary"])
        self.assertIn("confirmatory provider result is still pending", result["tools"][4]["summary"])
        self.assertIn("separately recorded", result["evidence_boundary"])

        status, headers, body = self.request(
            "GET", "/download/golden.json", token=None, origin=None, content_type=None
        )
        self.assertEqual(status, 200)
        self.assertIn("attachment", headers["content-disposition"])
        golden = json.loads(body)
        self.assertEqual(golden["demo"], "Merlin judging golden pass")
        creation = golden["judging_flow"][3]["result"]
        self.assertEqual(creation["chain_audit"]["checks_passed"], 15)
        self.assertFalse(golden["evidence_boundary"]["provider_native_skill_invocation_event"])

        status, _headers, body = self.request(
            "GET", "/control-room", token=None, origin=None, content_type=None
        )
        self.assertEqual(status, 200)
        self.assertIn(b"Merlin Control Room", body)

        status, _headers, body = self.request(
            "GET", "/report", token=None, origin=None, content_type=None
        )
        self.assertEqual(status, 200)
        self.assertIn(b'href="/control-room"', body)
        self.assertIn(b'href="/download/golden.json"', body)
        self.assertNotIn(b'controlled-lifecycle-control-room.html', body)

    def test_korean_incident_request_is_language_independent(self) -> None:
        state = self.post(
            "/api/message",
            {"message": "스킬 과부하를 진단하고 안전하게 복구해줘"},
        )
        self.assertEqual(state["status"], "complete")
        self.assertEqual(state["metrics"]["shadowing_before"], "89%")
        self.assertEqual(state["metrics"]["shadowing_after"], "0%")

    def test_unknown_request_guides_without_fabricating_a_run(self) -> None:
        state = self.post("/api/message", {"message": "Write a mobile app"})
        self.assertEqual(state["status"], "ready")
        self.assertIsNone(state["metrics"])
        self.assertIn("account-free judge sandbox", state["messages"][-1]["content"])
        status, _headers, body = self.request(
            "GET", "/report", token=None, origin=None, content_type=None
        )
        self.assertEqual(status, 409)
        self.assertEqual(json.loads(body)["error"]["code"], "report_pending")

    def test_user_trace_json_runs_observe_only_audit(self) -> None:
        state = self.post("/api/trace-audit", {"trace_bundle": sample_trace_bundle()})
        self.assertEqual(state["status"], "complete")
        self.assertTrue(state["actions"]["trace_audit_ready"])
        self.assertFalse(state["actions"]["report_ready"])
        result = state["messages"][-1]
        self.assertEqual(result["kind"], "trace_audit_result")
        self.assertEqual(result["audit_metrics"]["records"], 3)
        self.assertEqual(result["audit_metrics"]["candidates"], 1)
        self.assertIn("observe-only", result["evidence_boundary"])

        status, headers, body = self.request(
            "GET", "/download/trace-audit.json", token=None, origin=None, content_type=None
        )
        self.assertEqual(status, 200)
        self.assertIn("attachment", headers["content-disposition"])
        report = json.loads(body)
        self.assertFalse(report["safety"]["promotion_allowed"])
        self.assertFalse(report["safety"]["source_library_mutated"])

        status, _headers, body = self.request(
            "GET", "/download/trace-audit-sample.json", token=None, origin=None, content_type=None
        )
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body)["evidence_level"], "prompt_exposure")

    def test_reset_creates_a_fresh_incident(self) -> None:
        self.post(
            "/api/message",
            {"message": "Recover the overloaded skill library safely."},
        )
        status, _headers, body = self.request(
            "POST",
            "/api/message",
            {"message": "Recover the overloaded skill library safely."},
        )
        self.assertEqual(status, 409)
        self.assertEqual(json.loads(body)["error"]["code"], "incident_complete")
        state = self.post("/api/reset", {})
        self.assertEqual(state["status"], "ready")
        self.assertEqual(len(state["messages"]), 1)
        self.assertTrue(state["actions"]["send_allowed"])

    def test_html_is_self_contained_chat_first_and_accessible(self) -> None:
        status, headers, body = self.request(
            "GET", "/", token=None, origin=None, content_type=None
        )
        document = body.decode()
        self.assertEqual(status, 200)
        self.assertIn("default-src 'none'", headers["content-security-policy"])
        self.assertIn("Skills grow. Reliability should too.", document)
        self.assertIn("Built for agent-platform teams", document)
        self.assertIn("Beyond skill generation", document)
        self.assertIn("Run now · controlled", document)
        self.assertIn("Recorded · GPT-5.6", document)
        self.assertIn("Message Merlin", document)
        self.assertIn("Open Golden Report", document)
        self.assertIn("CONTROLLED RUNTIME · RECOVERY ACCEPTED", document)
        self.assertIn("RECORDED GPT-5.6 · CHAIN VERIFIED", document)
        self.assertIn("Audit your trace JSON", document)
        self.assertIn("Download trace sample", document)
        self.assertIn("/api/trace-audit", document)
        self.assertIn(
            '$("#message").value="Diagnose and safely recover this overloaded skill library.";\n    send();',
            document,
        )
        self.assertIn(".conversation{padding-bottom:260px}", document)
        self.assertIn('aria-live="polite"', document)
        self.assertIn('role="alert"', document)
        self.assertIn("@media(max-width:760px)", document)
        self.assertNotIn("https://", document)
        self.assertNotIn("/Users/", document)
        self.assertNotIn("/private/", document)
        self.assertNotIn("<script src=", document)

    def test_host_origin_token_content_type_size_and_shape_are_enforced(self) -> None:
        status, _headers, body = self.request(
            "GET", "/api/state", token=None, origin=None, content_type=None, host="localhost"
        )
        self.assertEqual(status, 400)
        self.assertEqual(json.loads(body)["error"]["code"], "invalid_host")

        cases = (
            ({"message": "x"}, "wrong", "valid", "application/json", 403, "invalid_origin"),
            ({"message": "x"}, "valid", "wrong", "application/json", 403, "invalid_token"),
            ({"message": "x"}, "valid", "valid", "text/plain", 415, "invalid_content_type"),
            ({"message": "x", "path": "/tmp"}, "valid", "valid", "application/json", 400, "invalid_shape"),
        )
        for payload, origin, token, content_type, expected, code in cases:
            with self.subTest(code=code):
                status, _headers, body = self.request(
                    "POST",
                    "/api/message",
                    payload,
                    origin=origin,
                    token=token,
                    content_type=content_type,
                )
                self.assertEqual(status, expected)
                self.assertEqual(json.loads(body)["error"]["code"], code)

        status, _headers, body = self.request(
            "POST",
            "/api/message",
            b"x",
            extra_headers={"Content-Length": str(MAX_JSON_BODY_BYTES + 1)},
        )
        self.assertEqual(status, 413)
        self.assertEqual(json.loads(body)["error"]["code"], "body_too_large")


class JudgeChatFactoryTests(unittest.TestCase):
    def test_factory_is_loopback_only_and_validates_port(self) -> None:
        with self.assertRaisesRegex(ValueError, "127.0.0.1"):
            create_judge_chat_server(host="0.0.0.0")
        for port in (-1, 65536, True, "0"):
            with self.subTest(port=port):
                with self.assertRaisesRegex(ValueError, "port"):
                    create_judge_chat_server(port=port)  # type: ignore[arg-type]


if __name__ == "__main__":
    unittest.main()

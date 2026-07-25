from __future__ import annotations

import copy
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from experiments.mvp.render_chat_campaign_report import (
    CampaignReportError,
    DEFAULT_EVIDENCE,
    main,
    render_report,
    validate_evidence,
    write_report,
)


class ChatCampaignReportTests(unittest.TestCase):
    def setUp(self) -> None:
        self.evidence = json.loads(DEFAULT_EVIDENCE.read_text(encoding="utf-8"))

    def test_packaged_evidence_renders_self_contained_review(self) -> None:
        document = render_report(self.evidence)

        self.assertIn("Merlin · Chat Lifecycle Review", document)
        self.assertIn("4/4", document)
        self.assertIn("100%", document)
        self.assertIn("route-local HIDE", document)
        self.assertIn("same verifier contract", document)
        self.assertIn("requested_cli_contract_only", document)
        self.assertIn("none reported", document)
        self.assertIn("no raw provider trace", document)
        self.assertNotIn("https://", document)
        self.assertNotIn("http://", document)
        self.assertNotIn("<script src=", document)

    def test_schema_rejects_metric_hash_order_and_boundary_tampering(self) -> None:
        mutations = []
        wrong_rate = copy.deepcopy(self.evidence)
        wrong_rate["baseline"]["exposure_shadowing_rate"] = 0.5
        mutations.append(wrong_rate)
        bad_hash = copy.deepcopy(self.evidence)
        bad_hash["baseline"]["routes"][0]["raw_trace_sha256"] = "bad"
        mutations.append(bad_hash)
        wrong_order = copy.deepcopy(self.evidence)
        wrong_order["provisional"]["routes"].reverse()
        mutations.append(wrong_order)
        fake_invocation = copy.deepcopy(self.evidence)
        fake_invocation["evidence_boundary"]["actual_invocation_evidence_complete"] = True
        mutations.append(fake_invocation)
        fake_decision = copy.deepcopy(self.evidence)
        fake_decision["lifecycle_decisions"][0]["route_risk_events"] = 3
        mutations.append(fake_decision)
        fake_model_evidence = copy.deepcopy(self.evidence)
        fake_model_evidence["runtime_contract"]["model_evidence_level"] = "provider_reported"
        mutations.append(fake_model_evidence)

        for mutation in mutations:
            with self.subTest(mutation=mutations.index(mutation)):
                with self.assertRaises(CampaignReportError):
                    validate_evidence(mutation)

    def test_untrusted_text_is_escaped(self) -> None:
        evidence = copy.deepcopy(self.evidence)
        evidence["title"] = "<script>alert(1)</script>"
        evidence["runtime_contract"]["cli_version"] = "<img src=x onerror=alert(1)>"

        document = render_report(evidence)

        self.assertNotIn("<img src=x", document)
        self.assertIn("&lt;img src=x", document)

    def test_writer_is_new_only_and_open_is_opt_in(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / "review.html"
            written = write_report(DEFAULT_EVIDENCE, output)
            self.assertEqual(written, output.resolve())
            self.assertTrue(output.is_file())
            with self.assertRaises(CampaignReportError):
                write_report(DEFAULT_EVIDENCE, output)

            second = Path(temporary) / "opened.html"
            with patch("experiments.mvp.render_chat_campaign_report.subprocess.run") as mocked:
                self.assertEqual(main(["--output", str(second), "--open"]), 0)
            mocked.assert_called_once_with(["open", str(second.resolve())], check=False)


if __name__ == "__main__":
    unittest.main()

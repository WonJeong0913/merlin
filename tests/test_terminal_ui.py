from __future__ import annotations

import io
import unittest

from src.merlin_harness.terminal_ui import TerminalUI


class TerminalUITests(unittest.TestCase):
    def make_ui(self, *, input_text: str = "") -> tuple[TerminalUI, io.StringIO]:
        output = io.StringIO()
        ui = TerminalUI(
            model="gpt-5.6-terra",
            effort="high",
            mode="live",
            autonomy="managed",
            workspace="/tmp/work",
            color=False,
            width=72,
            input_stream=io.StringIO(input_text),
            output_stream=output,
        )
        return ui, output

    def test_banner_exposes_agent_and_runtime_contract(self) -> None:
        ui, output = self.make_ui()
        ui.output("Merlin chat agent beta")
        rendered = output.getvalue()
        self.assertIn("MERLIN", rendered)
        self.assertIn("GOVERNED SKILL HARNESS", rendered)
        self.assertNotIn("THE KING", rendered)
        self.assertIn("LIVE  gpt-5.6-terra · high", rendered)
        self.assertIn("autonomy managed", rendered)

    def test_chat_and_harness_events_use_distinct_cards(self) -> None:
        ui, output = self.make_ui()
        ui.output("provisioned:")
        ui.output("  - report-writer: exact artifact contract")
        ui.output("assistant> I created the report safely.")
        rendered = output.getvalue()
        self.assertIn("MERLIN · PROVISION", rendered)
        self.assertIn("report-writer", rendered)
        self.assertIn("MERLIN", rendered)
        self.assertIn("I created the report safely.", rendered)

    def test_safe_stop_and_evidence_are_visually_separate(self) -> None:
        ui, output = self.make_ui()
        ui.output("turn failed safely: verifier mismatch")
        ui.output('{"accepted": false}')
        rendered = output.getvalue()
        self.assertIn("MERLIN · SAFE STOP", rendered)
        self.assertIn("MERLIN · EVIDENCE", rendered)
        self.assertIn('"accepted": false', rendered)

    def test_input_uses_terminal_composer_without_changing_value(self) -> None:
        ui, output = self.make_ui(input_text="스킬 상태를 보여줘\n")
        self.assertEqual(ui.input(), "스킬 상태를 보여줘")
        rendered = output.getvalue()
        self.assertIn("YOU", rendered)
        self.assertIn("스킬 상태를 보여줘", rendered)


if __name__ == "__main__":
    unittest.main()

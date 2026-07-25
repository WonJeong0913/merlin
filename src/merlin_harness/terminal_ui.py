"""Dependency-free terminal presentation for Merlin's chat agent.

The renderer deliberately stays above the session and evidence layers.  It
never rewrites prompts, tool results, hashes, or lifecycle decisions; it only
turns the existing text stream into a compact Claude Code/Codex-style layout.
"""

from __future__ import annotations

import builtins
import shutil
import sys
import textwrap
from dataclasses import dataclass
from typing import TextIO


@dataclass(frozen=True, slots=True)
class TerminalTheme:
    cyan: str = "\x1b[38;5;81m"
    green: str = "\x1b[38;5;78m"
    amber: str = "\x1b[38;5;215m"
    red: str = "\x1b[38;5;203m"
    blue: str = "\x1b[38;5;111m"
    muted: str = "\x1b[38;5;245m"
    bold: str = "\x1b[1m"
    reset: str = "\x1b[0m"


class TerminalUI:
    """Render the existing REPL stream without changing its contracts."""

    MIN_WIDTH = 54
    MAX_WIDTH = 96

    def __init__(
        self,
        *,
        model: str,
        effort: str,
        mode: str,
        autonomy: str,
        workspace: str,
        color: bool = True,
        width: int | None = None,
        input_stream: TextIO | None = None,
        output_stream: TextIO | None = None,
    ) -> None:
        detected = shutil.get_terminal_size(fallback=(88, 24)).columns
        self.width = max(self.MIN_WIDTH, min(width or detected, self.MAX_WIDTH))
        self.model = model
        self.effort = effort
        self.mode = mode
        self.autonomy = autonomy
        self.workspace = workspace
        self.color = color
        self.input_stream = input_stream or sys.stdin
        self.output_stream = output_stream or sys.stdout
        self.theme = TerminalTheme()
        self._banner_rendered = False
        self._provisioning_open = False

    def _style(self, value: str, *codes: str) -> str:
        if not self.color or not codes:
            return value
        return "".join(codes) + value + self.theme.reset

    def _emit(self, value: str = "") -> None:
        self.output_stream.write(value + "\n")
        self.output_stream.flush()

    def _wrapped_lines(self, value: str, *, indent: int = 0) -> list[str]:
        available = max(20, self.width - 4 - indent)
        rendered: list[str] = []
        for raw_line in value.splitlines() or [""]:
            if not raw_line:
                rendered.append("")
                continue
            rendered.extend(
                textwrap.wrap(
                    raw_line,
                    width=available,
                    replace_whitespace=False,
                    drop_whitespace=False,
                    break_long_words=False,
                    break_on_hyphens=False,
                )
                or [""]
            )
        return rendered

    def _box(self, title: str, body: str, *, tone: str = "cyan") -> None:
        palette = {
            "cyan": self.theme.cyan,
            "green": self.theme.green,
            "amber": self.theme.amber,
            "red": self.theme.red,
            "blue": self.theme.blue,
        }
        color = palette.get(tone, self.theme.cyan)
        label = f" {title} "
        top = "╭─" + label + "─" * max(1, self.width - len(label) - 2)
        bottom = "╰" + "─" * (self.width - 1)
        self._emit(self._style(top, color, self.theme.bold))
        for line in self._wrapped_lines(body):
            self._emit(self._style("│", color) + (f" {line}" if line else ""))
        self._emit(self._style(bottom, color))

    def render_banner(self) -> None:
        if self._banner_rendered:
            return
        self._banner_rendered = True
        brand = "MERLIN"
        subtitle = "GOVERNED SKILL HARNESS"
        self._emit()
        self._emit(self._style(f"  ✦  {brand}", self.theme.cyan, self.theme.bold))
        self._emit(self._style(f"     {subtitle}", self.theme.muted))
        self._emit(self._style("  " + "─" * (self.width - 2), self.theme.muted))
        self._emit(
            "  "
            + self._style(self.mode.upper(), self.theme.green, self.theme.bold)
            + self._style(f"  {self.model} · {self.effort}", self.theme.muted)
        )
        self._emit(
            self._style(
                f"  autonomy {self.autonomy} · workspace {self.workspace}",
                self.theme.muted,
            )
        )
        self._emit()

    def input(self, _prompt: str = "you> ") -> str:
        """Read one user turn with a stable two-line composer."""

        self._provisioning_open = False
        self._emit(self._style("╭─ YOU", self.theme.blue, self.theme.bold))
        prompt = self._style("╰─ ", self.theme.blue)
        if self.input_stream is sys.stdin:
            return builtins.input(prompt)
        self.output_stream.write(prompt)
        self.output_stream.flush()
        value = self.input_stream.readline()
        if value == "":
            raise EOFError
        self.output_stream.write(value)
        self.output_stream.flush()
        return value.rstrip("\n")

    def output(self, message: str) -> None:
        """Classify one existing REPL message and render it as a terminal event."""

        message = str(message)
        if message == "Merlin chat agent beta":
            self.render_banner()
            return
        if message.startswith("assistant> "):
            self._provisioning_open = False
            self._box("MERLIN", message.removeprefix("assistant> "), tone="green")
            self._emit()
            return
        if message == "provisioned:":
            self._provisioning_open = True
            self._emit(self._style("  MERLIN · PROVISION", self.theme.cyan, self.theme.bold))
            return
        if self._provisioning_open and message.startswith("  - "):
            self._emit(self._style("  ├─ ", self.theme.cyan) + message[4:])
            return
        if message.startswith("provisioned: none"):
            self._provisioning_open = False
            self._emit(
                self._style("  MERLIN · PROVISION  ", self.theme.cyan, self.theme.bold)
                + self._style("abstain", self.theme.muted)
            )
            return
        if message.startswith(("autonomy>", "learning>")):
            label = "MERLIN · AUTONOMY" if message.startswith("autonomy>") else "MERLIN · LEARN"
            body = message.split(">", 1)[1].strip()
            self._box(label, body, tone="amber")
            return
        lowered = message.casefold()
        if any(token in lowered for token in ("blocked:", "failed safely:", " error:")):
            self._provisioning_open = False
            self._box("MERLIN · SAFE STOP", message, tone="red")
            return
        if message.startswith(("{", "[")) and message.rstrip().endswith(("}", "]")):
            self._provisioning_open = False
            self._box("MERLIN · EVIDENCE", message, tone="blue")
            return
        if message.startswith("╔"):
            self._provisioning_open = False
            self._emit(self._style(message, self.theme.cyan))
            return
        if message.startswith("ARTIFACT  "):
            self._emit(self._style("  ✓ " + message, self.theme.green))
            return
        if message == "bye":
            self._emit(self._style("  session closed", self.theme.muted))
            return
        if message.startswith(("OFFLINE JUDGE MODE", "Frozen Codex backend")):
            self._emit(self._style("  ● " + message, self.theme.green))
            return
        if message.startswith(("Managed autonomy", "Strict autonomy")):
            self._emit(self._style("  ◇ " + message, self.theme.amber))
            return
        if message.startswith("Verified "):
            self._emit(self._style("  ✓ " + message, self.theme.green))
            return
        if message.startswith(("Prompt provisioning", "Try:", "Type /help")):
            self._emit(self._style("  " + message, self.theme.muted))
            return
        self._provisioning_open = False
        self._emit(self._style("  ◆ ", self.theme.cyan) + message)

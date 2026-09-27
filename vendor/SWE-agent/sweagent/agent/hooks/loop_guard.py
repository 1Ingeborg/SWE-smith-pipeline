"""Reject repeated identical actions so a looping model cannot burn its budget.

A model served through an OpenAI-compatible endpoint can collapse into a fixed
point: once a command comes back with no output, decoding over an unchanged
observation window reproduces the same action indefinitely. The rollout then
spends every remaining call re-running that one command and never edits a file,
so the instance finishes with an empty patch.

This mirrors the guard already protecting the mini-SWE-agent path
(``LFCompatibleLitellmModel`` under ``/data/configs/mini-sweagent``), including
the window size, the duplicate threshold and the warning text, so the two
harnesses stay comparable.
"""

from __future__ import annotations

import shlex
from collections import deque

from sweagent.agent.hooks.abstract import AbstractAgentHook
from sweagent.types import StepOutput
from sweagent.utils.log import get_logger


class LoopGuardAgentHook(AbstractAgentHook):
    """Refuse to execute an action that was already proposed ``threshold`` times.

    Runs on ``on_actions_generated``, which fires after the model's action is
    parsed but before ``handle_action`` executes it, so rewriting
    ``step.action`` here changes what actually runs. The original action is kept
    in ``step.extra_info`` so interventions are visible in the trajectory.
    """

    def __init__(self, window: int = 12, threshold: int = 3):
        self._recent: deque[str] = deque(maxlen=window)
        self._threshold = threshold
        self.n_interventions = 0
        self._logger = get_logger("loop-guard", emoji="🔁")

    def on_actions_generated(self, *, step: StepOutput):
        action = (step.action or "").strip()
        if not action:
            return
        duplicate_count = sum(1 for old in self._recent if old == action) + 1
        self._recent.append(action)
        if duplicate_count < self._threshold:
            return

        self.n_interventions += 1
        step.extra_info["loop_intervention"] = {
            "duplicate_count": duplicate_count,
            "original_action": step.action,
        }
        warning = (
            "LOOP_DETECTED: You proposed the identical bash action "
            f"{duplicate_count} times within the recent action window. "
            "It was not run again. "
            "Use the existing observation, change your approach, and issue a "
            "different command that makes progress toward editing and testing the fix."
        )
        step.action = "printf '%s\\n' " + shlex.quote(warning)
        self._logger.warning(
            "LOOP_DETECTED: identical action proposed %d times; blocked it and returned a warning instead",
            duplicate_count,
        )

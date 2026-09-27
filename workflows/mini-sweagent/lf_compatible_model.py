"""LLaMA Factory compatibility adapter for mini-SWE-agent 2.x.

mini-SWE-agent normally stores only the corrective user message when a model
response contains no valid tool call.  Strict chat templates then see two
non-alternating input-side messages (for example ``tool -> user``).  Preserve
the failed assistant response before the corrective feedback so the next API
request remains a valid conversation.
"""

from __future__ import annotations

import json
import re
import shlex
from collections import deque
from typing import Any

from openai import InternalServerError, OpenAI
from minisweagent.exceptions import FormatError
from minisweagent.models.litellm_model import LitellmModel
from minisweagent.models.litellm_textbased_model import LitellmTextbasedModel


class LFCompatibleLitellmModel(LitellmModel):
    """Persist malformed assistant turns before mini's format feedback."""

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._recent_commands: deque[tuple[str, ...]] = deque(maxlen=12)

    def _parse_actions(self, response: Any) -> list[dict[str, Any]]:
        """Accept Qwen's textual ``<tool_call>`` fallback as a bash action.

        LLaMA Factory normally exposes Qwen tool calls through the OpenAI
        ``tool_calls`` field.  For longer/quoted commands the same model can
        instead return the template-native XML tag in ``content``.  Recover
        those commands before mini-SWE-agent treats the response as empty.
        """
        message = response.choices[0].message
        if message.tool_calls:
            return super()._parse_actions(response)

        content = message.content or ""
        blocks = re.findall(r"<tool_call>\s*(.*?)\s*</tool_call>", content, re.DOTALL)
        actions: list[dict[str, Any]] = []
        for block in blocks:
            try:
                try:
                    call = json.loads(block)
                except json.JSONDecodeError:
                    # Qwen occasionally closes ``arguments`` but omits the
                    # outer call object's final brace.  Accept exactly that
                    # one-character repair; do not guess at broader damage.
                    call = json.loads(block + "}")
                if call.get("name") != "bash":
                    continue
                arguments = call.get("arguments", {})
                if isinstance(arguments, str):
                    arguments = json.loads(arguments)
                command = arguments.get("command") if isinstance(arguments, dict) else None
                if isinstance(command, str) and command.strip():
                    # No tool_call_id is intentional: mini formats the command
                    # result as a user observation, matching the textual turn.
                    actions.append({"command": command})
            except (json.JSONDecodeError, AttributeError, TypeError):
                continue
        if actions:
            return actions
        return super()._parse_actions(response)

    def query(self, messages: list[dict[str, Any]], **kwargs: Any) -> dict:
        try:
            message = super().query(messages, **kwargs)
        except FormatError as exc:
            if not exc.messages:
                raise

            feedback = exc.messages[0]
            extra = feedback.get("extra", {})
            response = extra.get("response")
            if not isinstance(response, dict):
                raise

            try:
                raw_message = response["choices"][0]["message"]
            except (KeyError, IndexError, TypeError):
                raise
            if not isinstance(raw_message, dict):
                raise

            assistant: dict[str, Any] = {
                "role": "assistant",
                "content": raw_message.get("content") or "",
            }
            tool_calls = raw_message.get("tool_calls")
            if tool_calls:
                assistant["tool_calls"] = tool_calls

            # DefaultAgent accounts for a failed call using the first message.
            # Move only accounting metadata there; keep the full response on
            # the feedback message for trajectory/debugging purposes.
            assistant["extra"] = {
                "cost": extra.get("cost", 0.0),
                "format_error": True,
            }
            extra["cost"] = 0.0
            exc.messages = (assistant, feedback)
            raise

        actions = message.get("extra", {}).get("actions", [])
        commands = tuple(a.get("command", "") for a in actions)
        duplicate_count = sum(old == commands for old in self._recent_commands) + 1
        if commands:
            self._recent_commands.append(commands)

        if actions and duplicate_count >= 3:
            warning = (
                "LOOP_DETECTED: You proposed the identical bash action "
                f"{duplicate_count} times within the recent action window. "
                "It was not run again. "
                "Use the existing observation, change your approach, and issue a "
                "different command that makes progress toward editing and testing the fix."
            )
            safe_command = "printf '%s\\n' " + shlex.quote(warning)
            original_actions = [dict(action) for action in actions]
            for action in actions:
                action["command"] = safe_command
            message["extra"]["loop_intervention"] = {
                "duplicate_count": duplicate_count,
                "original_actions": original_actions,
            }
        return message


class LFCompatibleTextbasedModel(LitellmTextbasedModel, LFCompatibleLitellmModel):
    """Text-command mini model with the same history and loop safeguards."""
    abort_exceptions = [*LitellmModel.abort_exceptions, InternalServerError]

    def _calculate_cost(self, response: Any) -> dict[str, float]:
        """Local endpoint has no billable API cost; avoid LiteLLM price lookup."""
        return {"cost": 0.0}

    def _query(self, messages: list[dict[str, Any]], **kwargs: Any) -> Any:
        """Call the OpenAI-compatible endpoint without LiteLLM's worker pool.

        A fresh client per turn deliberately avoids reusing a half-closed SSH
        tunnel socket.  The returned OpenAI object implements the same fields
        and ``model_dump`` method consumed by mini-SWE-agent.
        """
        call_kwargs = dict(self.config.model_kwargs)
        call_kwargs.update(kwargs)
        base_url = call_kwargs.pop("api_base", None) or call_kwargs.pop("base_url", None)
        api_key = call_kwargs.pop("api_key", "swesmith")
        timeout = call_kwargs.pop("timeout", 90)
        call_kwargs.pop("drop_params", None)
        call_kwargs.pop("parallel_tool_calls", None)
        model_name = self.config.model_name.split("/", 1)[-1]
        with OpenAI(
            base_url=base_url,
            api_key=api_key,
            timeout=timeout,
            max_retries=0,
        ) as client:
            return client.chat.completions.create(
                model=model_name,
                messages=messages,
                **call_kwargs,
            )

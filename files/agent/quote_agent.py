"""The ADK agent and the adapter that lets the eval harness drive it.

The adapter matters more than it looks: `quote(enquiry, attachments)` has the
exact signature the harness expects, so the same agent object is scored by the
same judge whether it is running locally, in the GEPA loop, or behind Cloud Run.
No separate eval-only code path means no chance of evaluating something other
than what you ship.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import uuid
from typing import Any

from google.adk.agents import LlmAgent
from google.adk.runners import InMemoryRunner
from google.genai import types

from .prompt import SEED
from .tools import TOOLS

APP_NAME = "quote_runner"
MODEL = os.environ.get("QR_MODEL", "gemini-3.5-flash")


def build_agent(instruction: str | None = None, model: str | None = None) -> LlmAgent:
    """Build the quoting agent.

    `instruction` is injectable so the GEPA coach can hand in a rewritten
    prompt without touching this file.
    """
    return LlmAgent(
        name="quoting_engineer",
        model=model or MODEL,
        instruction=instruction or SEED,
        description="Turns a messy fabrication enquiry into a priced quote or a question.",
        tools=TOOLS,
    )


# --------------------------------------------------------------------------
# Output parsing
# --------------------------------------------------------------------------

_FENCE = re.compile(r"```(?:json)?\s*(.*?)\s*```", re.S)


def parse_quote(text: str) -> dict[str, Any]:
    """Pull the JSON verdict out of the model's final message.

    Models add fences and prose no matter how firmly you ask them not to.
    Failing to parse must not look like a cheap quote, so an unparseable reply
    becomes an explicit failure rather than a price of 0.
    """
    candidate = text.strip()
    if m := _FENCE.search(candidate):
        candidate = m.group(1)
    elif (start := candidate.find("{")) != -1 and (end := candidate.rfind("}")) != -1:
        candidate = candidate[start : end + 1]

    try:
        data = json.loads(candidate)
    except json.JSONDecodeError:
        return {"price": 0.0, "promised_lead_days": None, "escalated": False,
                "parse_error": True, "raw": text[:500]}

    return {
        "price": float(data.get("price") or 0.0),
        "promised_lead_days": data.get("lead_days"),
        "escalated": bool(data.get("escalate", False)),
        "reasoning": data.get("reasoning", ""),
        "question": data.get("question"),
        "parse_error": False,
    }


# --------------------------------------------------------------------------
# Harness adapter
# --------------------------------------------------------------------------


class QuoteRunnerAgent:
    """Wraps the ADK agent in the `(enquiry, attachments) -> dict` contract."""

    def __init__(self, instruction: str | None = None, model: str | None = None):
        self.agent = build_agent(instruction, model)
        self.runner = InMemoryRunner(agent=self.agent, app_name=APP_NAME)

    async def quote_async(self, enquiry: str, attachments: list[str] | None = None) -> dict:
        attachments = attachments or []
        user_id = "eval"
        session = await self.runner.session_service.create_session(
            app_name=APP_NAME, user_id=user_id, session_id=str(uuid.uuid4())
        )

        prompt = enquiry
        if attachments:
            # Filenames only. Real file bytes land here on day 6 -- for now the
            # agent should treat a filename as evidence a file exists, not as
            # evidence of what is in it.
            prompt += f"\n\n[Attached files: {', '.join(attachments)}]"

        final = ""
        tool_calls: list[str] = []
        async for event in self.runner.run_async(
            user_id=user_id,
            session_id=session.id,
            new_message=types.Content(role="user", parts=[types.Part(text=prompt)]),
        ):
            if event.content and event.content.parts:
                for part in event.content.parts:
                    if getattr(part, "function_call", None):
                        tool_calls.append(part.function_call.name)
                    if getattr(part, "text", None) and event.is_final_response():
                        final += part.text

        result = parse_quote(final)
        result["tool_calls"] = tool_calls
        # An agent that quoted without ever costing the job got there by guessing.
        result["priced_without_tool"] = "price_job" not in tool_calls
        return result

    def quote(self, enquiry: str, attachments: list[str] | None = None) -> dict:
        return asyncio.run(self.quote_async(enquiry, attachments))


def make_agent_fn(instruction: str | None = None, model: str | None = None):
    """Returns something the eval harness can call directly."""
    agent = QuoteRunnerAgent(instruction, model)
    return lambda enquiry, attachments: agent.quote(enquiry, attachments)

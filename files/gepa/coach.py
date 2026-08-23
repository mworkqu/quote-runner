"""Coaches: they propose a rewritten instruction from failure traces.

A coach is handed a list of `FailureTrace` and the instruction that produced
them, and returns a NEW instruction string. That is the whole contract. It
never receives a `Case`, a `Job`, or anything that reads ground truth.

WHAT THE COACH IS ALLOWED TO SEE

`FailureTrace` carries exactly three things per failing case:

  enquiry    the visible client text — the same string the agent saw
  returned   what the agent actually produced (price, lead, escalation, prose)
  reasons    the honest judge's `reasons` for the failure

and nothing else. There is deliberately no `job` field, no `true_cost`, no
`price_floor`, no `blockers`. If the coach could see ground truth it would write
prompts that memorise the eval set — "when the enquiry says 'manifold', quote
905" — and the eval score would climb while the agent learned nothing. The
absence of a `job` attribute is the enforcement: it is not a rule the coach is
asked to follow, it is a field it does not have.

The judge's `reasons` are the one ground-truth-derived signal that IS allowed
through, because that is the feedback a coach legitimately learns from — the
same sentence a human reviewer would read off the scoreboard ("price 180 is
120 below the floor"). It describes the verdict, not the answer key.

TWO COACHES

`StubCoach` is deterministic and offline: it reads the failing enquiries, and
appends a text-triggered escalation rule for a discriminating phrase it finds
in jobs the agent quoted but could not deliver. It spends no Vertex quota and
exists so the whole loop can be exercised end to end (see `loop.py --dry-run`).

`LlmCoach` is the real reflective coach: it hands the failures to a model and
asks for a rewritten instruction. It is only reachable on a real run.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Protocol, Sequence

__all__ = ["FailureTrace", "Coach", "StubCoach", "LlmCoach"]


@dataclass(frozen=True)
class FailureTrace:
    """One failing case, reduced to what a coach may see.

    There is no `job` field, and that is the point: the coach cannot memorise an
    answer it is never given. `returned` is the agent's own output; `reasons`
    are the judge's verdict text.
    """

    case_id: str
    enquiry: str
    returned: dict
    reasons: tuple[str, ...] = ()
    attachments: tuple[str, ...] = ()

    def as_block(self) -> str:
        """Render this trace for an LLM coach. Visible signals only."""
        ret = self.returned
        shape = (
            f"escalated={ret.get('escalated', False)}, "
            f"price={ret.get('price')}, lead_days={ret.get('promised_lead_days')}"
        )
        prose = (ret.get("reasoning") or ret.get("question") or "").strip()
        lines = [
            f"CASE {self.case_id}",
            f"  enquiry: {self.enquiry.strip()}",
        ]
        if self.attachments:
            lines.append(f"  attachments: {', '.join(self.attachments)}")
        lines.append(f"  agent returned: {shape}")
        if prose:
            lines.append(f"  agent said: {prose}")
        for r in self.reasons:
            lines.append(f"  judge: {r}")
        return "\n".join(lines)


class Coach(Protocol):
    """A coach turns (instruction, failures) into a new instruction."""

    name: str

    def propose(
        self,
        instruction: str,
        failures: Sequence[FailureTrace],
        *,
        generation: int,
        seed: int,
    ) -> str: ...


# ---------------------------------------------------------------------------
# Stub coach — deterministic, offline, used by --dry-run
# ---------------------------------------------------------------------------

# Phrases that, in the failing enquiries, mark a job that reads quotable but is
# not. Each is deliberately DISCRIMINATING — it appears in exactly one family of
# un-deliverable enquiry and never in a quotable one — so a rule keyed off it
# escalates the right case without tripping a good one. This is the offline
# stand-in for the judgement a real coach would make; it is not meant to be
# clever, only deterministic and directionally correct.
_DISCRIMINATIVE_PHRASES: tuple[str, ...] = ("manifold", "carbon fibre", "lamp shade")

# Substrings of the honest judge's reasons that mean "this should have been an
# escalation, and the agent quoted instead".
_ESCALATION_MARKERS: tuple[str, ...] = (
    "cannot be delivered",
    "cannot be made",
    "deliver in time",
    "does not run",
    "exceeds the",
    "escalat",
    "were never established",
    "cannot be confirmed to fit",
)

_RULE_PREFIX = "ESCALATE-WHEN:"


def existing_escalation_phrases(instruction: str) -> set[str]:
    """The phrases the instruction already keys an escalation rule off."""
    out: set[str] = set()
    for line in instruction.splitlines():
        s = line.strip()
        if s.upper().startswith(_RULE_PREFIX):
            out.add(s[len(_RULE_PREFIX):].strip().lower())
    return out


@dataclass
class StubCoach:
    """Offline reflection: append one text-triggered escalation rule per gen.

    It ranks the discriminating phrases by how many *should-have-escalated*
    failures mention them, and adds the winner as an `ESCALATE-WHEN:` line the
    stub agent honours. When no failing enquiry matches any remaining phrase it
    proposes no change — a legitimate no-op generation, which is exactly what a
    coach should do when it has nothing to learn from the failures in front of
    it (the case the gameable run leans on).
    """

    seed: int = 7
    name: str = field(default="stub")

    def propose(
        self,
        instruction: str,
        failures: Sequence[FailureTrace],
        *,
        generation: int,
        seed: int | None = None,
    ) -> str:
        already = existing_escalation_phrases(instruction)
        counts: dict[str, int] = {}
        for phrase in _DISCRIMINATIVE_PHRASES:
            if phrase in already:
                continue
            hits = sum(
                1
                for f in failures
                if phrase in f.enquiry.lower()
                and any(m in " ".join(f.reasons).lower() for m in _ESCALATION_MARKERS)
            )
            if hits:
                counts[phrase] = hits

        if not counts:
            # Nothing to learn from these failures. Return the instruction
            # unchanged; the loop records a no-op generation.
            return instruction

        # Most-supported phrase wins; ties break by declaration order so the run
        # is fully deterministic given the seed.
        best = max(counts, key=lambda p: (counts[p], -_DISCRIMINATIVE_PHRASES.index(p)))
        note = (
            f"# gen {generation}: {counts[best]} enquiry(ies) were quoted but could "
            f"not be delivered; escalate ones like this instead."
        )
        return f"{instruction.rstrip()}\n\n{note}\n{_RULE_PREFIX} {best}\n"


# ---------------------------------------------------------------------------
# LLM coach — the real reflective rewrite, only reachable on a real run
# ---------------------------------------------------------------------------

_COACH_SYSTEM = """You are optimising the INSTRUCTION given to a quoting agent for
a fabrication workshop. You are shown the current instruction and a set of cases
the agent got wrong: for each, the client enquiry the agent saw, what the agent
returned, and the judge's reasons for failing it.

Rewrite the instruction so the agent would handle these failures correctly,
WITHOUT overfitting. Rules:

- You are given enquiries and verdicts, never the ground-truth answer. Do not
  invent specific prices, dimensions, or per-case rules ("when the client says
  X, quote Y"). Fix the agent's REASONING, not its lookup table.
- Keep every constraint that is already working. Do not delete the tool-calling
  process or the JSON output contract.
- Output ONLY the new instruction text. No preamble, no code fence, no
  commentary."""


@dataclass
class LlmCoach:
    """Hands the failures to a model and asks for a rewritten instruction.

    Only used on a real run; it lazy-imports the Google stack so the dry-run
    path never needs Vertex installed or configured.
    """

    model: str = field(default_factory=lambda: os.environ.get("QR_COACH_MODEL", "gemini-3.5-flash"))
    max_failures: int = 12
    seed: int = 7
    name: str = field(default="llm")

    def propose(
        self,
        instruction: str,
        failures: Sequence[FailureTrace],
        *,
        generation: int,
        seed: int | None = None,
    ) -> str:
        if not failures:
            return instruction  # nothing failed; no reason to rewrite

        try:
            from google import genai
            from google.genai import types
        except ImportError as e:  # pragma: no cover - real-run only
            raise RuntimeError(
                "LlmCoach needs the Google GenAI SDK and Vertex configuration. "
                "Install requirements and set GOOGLE_CLOUD_PROJECT / "
                "GOOGLE_CLOUD_LOCATION / GOOGLE_GENAI_USE_VERTEXAI, or use --dry-run."
            ) from e

        blocks = "\n\n".join(f.as_block() for f in list(failures)[: self.max_failures])
        user = (
            f"CURRENT INSTRUCTION\n-------------------\n{instruction}\n\n"
            f"FAILURES ({len(failures)} cases; showing up to {self.max_failures})\n"
            f"--------\n{blocks}\n\n"
            "Return the rewritten instruction only."
        )

        client = genai.Client(  # pragma: no cover - real-run only
            vertexai=os.environ.get("GOOGLE_GENAI_USE_VERTEXAI", "TRUE").upper() == "TRUE",
            project=os.environ.get("GOOGLE_CLOUD_PROJECT"),
            location=os.environ.get("GOOGLE_CLOUD_LOCATION", "us-central1"),
        )
        resp = client.models.generate_content(
            model=self.model,
            contents=user,
            config=types.GenerateContentConfig(
                system_instruction=_COACH_SYSTEM,
                temperature=0.7,
                seed=seed if seed is not None else self.seed,
            ),
        )
        text = (resp.text or "").strip()
        if not text:
            # A blank rewrite must never silently erase the instruction.
            return instruction
        return text

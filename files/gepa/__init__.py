"""GEPA — the instruction optimiser.

It evolves ONE string, `agent/prompt.py:SEED`, against the eval set, writing each
generation to `gepa/prompts/gen_<n>.txt`. It never edits the costing engine, the
cases, or the judge — that is enforced by a hash guard, not by good intentions
(see `loop.ProtectedTree`).

    python3 -m gepa.loop --dry-run                 # stub coach + stub agent
    python3 -m gepa.loop --judge honest            # real run, honest judge
    python3 -m gepa.loop --judge gameable          # the divergence control

The public surface is small on purpose. `optimise()` runs the loop over dev
cases; `holdout_score()` is the separate one-shot that touches the held-out set.
"""

from typing import Any

from .coach import Coach, FailureTrace, LlmCoach, StubCoach

_LAZY = {
    "PROMPTS_DIR",
    "OptimizeResult",
    "ProtectedTree",
    "holdout_score",
    "optimise",
    "stub_agent_fn",
}


def __getattr__(name: str) -> Any:
    """Import `loop` on demand.

    Importing it eagerly here means `python3 -m gepa.loop` loads the module
    twice — once through this package, once as `__main__` — and Python warns
    about it on every run. `evals/__init__.py` does the same thing for the same
    reason.
    """
    if name in _LAZY:
        from . import loop

        return getattr(loop, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


__all__ = [
    "Coach",
    "FailureTrace",
    "StubCoach",
    "LlmCoach",
    "optimise",
    "holdout_score",
    "stub_agent_fn",
    "OptimizeResult",
    "ProtectedTree",
    "PROMPTS_DIR",
]

"""The eval set and its harness.

    from evals.cases import CASES, DEV_CASES, HELD_OUT_CASES
    from evals.harness import score_case, run_eval, oracle_quote

Two things worth remembering about this package:

1. `CASES` holds ground truth. Agents get `case.enquiry` and
   `case.attachments` and nothing else — the boundary is enforced in exactly
   one function, `harness._from_agent`.

2. Run `python3 -m evals.harness --validate` before every eval. If the oracle
   does not score 100%, the fault is in the case, not the agent.
"""

from typing import Any

from .cases import CASES, DEV_CASES, HELD_OUT_CASES, Case, by_id, case_ids

_LAZY = {"score_case", "run_eval", "oracle_quote", "naive_quote", "RESULTS_DIR"}


def __getattr__(name: str) -> Any:
    """Import `harness` on demand.

    Importing it eagerly here means `python3 -m evals.harness` loads the module
    twice — once via this package, once as `__main__` — and Python warns about
    it on every single run. Lazily is the fix, and it also keeps `from evals
    import CASES` from dragging in argparse.
    """
    if name in _LAZY:
        from . import harness

        return getattr(harness, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


__all__ = [
    "Case",
    "CASES",
    "DEV_CASES",
    "HELD_OUT_CASES",
    "by_id",
    "case_ids",
    "score_case",
    "run_eval",
    "oracle_quote",
    "naive_quote",
]

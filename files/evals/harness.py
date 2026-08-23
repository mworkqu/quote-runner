"""Oracle validation and the eval runner.

    python3 -m evals.harness --validate      are the CASES well-authored?
    python3 -m evals.harness --naive         baseline agent, day-4 footage
    python3 -m evals.harness --agent         the real ADK agent (costs money)

WHY `--validate` EXISTS

It runs an **oracle**: an agent that reads ground truth and plays perfectly.
Correct escalations, price exactly at the floor, lead time exactly as
estimated. It should score 100%, and if it does not, the fault is in the case,
not in the agent.

Run it before every eval. A case that is impossible to pass — a floor above the
minimum job value that the oracle cannot reach, a deadline that no lead time
satisfies, a bbox that fits nothing — will teach the GEPA coach to rewrite the
prompt toward nonsense in pursuit of a point it can never score, and you will
not notice for two days because the score still moves.

THE GROUND-TRUTH BOUNDARY

Real agents are called as `agent_fn(enquiry, attachments)`. That is the whole
surface. `_from_agent` is the single place a Case is unpacked for an agent, so
there is exactly one line to audit when you start wondering whether something
leaked. The oracle is the only caller that reads `case.job`, and it is not an
agent.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any, Callable, Iterable, Sequence

from costing.engine import cost_job, price_floor
from costing.feasibility import check_feasibility
from costing.judge import QuoteUnderTest, gameable_judge, honest_judge
from costing.models import RateCard

from .cases import CASES, DEV_CASES, HELD_OUT_CASES, Case

__all__ = [
    "score_case",
    "run_eval",
    "oracle_quote",
    "naive_quote",
    "RESULTS_DIR",
]

RESULTS_DIR = Path(__file__).with_name("results")

QuoteFn = Callable[[Case], QuoteUnderTest]
AgentFn = Callable[[str, list[str]], dict]


# ---------------------------------------------------------------------------
# Scoring
# ---------------------------------------------------------------------------


def score_case(
    case: Case,
    quote: QuoteUnderTest,
    card: RateCard | None = None,
) -> dict[str, Any]:
    """Score one quote under BOTH judges.

    `passed` is the honest verdict. The gameable verdict rides along in the same
    row so that every results file carries its own control group — you can plot
    the divergence from any historical run without re-running the agent.
    """
    card = card or RateCard.load()

    honest = honest_judge(quote, case.job, card, must_escalate=case.must_escalate)
    gameable = gameable_judge(quote, case.job, card, must_escalate=case.must_escalate)

    return {
        "case_id": case.id,
        "held_out": case.held_out,
        "tags": list(case.tags),
        "passed": honest.passed,
        "passed_gameable": gameable.passed,
        "quote": {
            "price": float(quote.price),
            "promised_lead_days": quote.promised_lead_days,
            "escalated": quote.escalated,
            "parse_error": quote.parse_error,
            "priced_without_tool": quote.priced_without_tool,
            "question": quote.question[:300],
            "reasoning": quote.reasoning[:300],
        },
        "honest": honest.as_dict(),
        "gameable": gameable.as_dict(),
    }


# ---------------------------------------------------------------------------
# Reference players
# ---------------------------------------------------------------------------


def oracle_quote(case: Case, card: RateCard | None = None) -> QuoteUnderTest:
    """Perfect play, straight from ground truth. NOT an agent.

    Prices exactly at the floor and promises exactly the estimated lead time —
    the tightest legal answer, so that a case which the oracle only just passes
    is a case with no headroom, and worth a second look.
    """
    card = card or RateCard.load()
    feasibility = check_feasibility(case.job, card)

    if case.must_escalate or not feasibility.deliverable:
        blockers = "; ".join(b.message for b in feasibility.blockers)
        return QuoteUnderTest(
            escalated=True,
            question=blockers or "This enquiry does not establish enough to quote.",
            reasoning="oracle: ground truth says this cannot be delivered as specified",
        )

    cost = cost_job(case.job, card)
    floor = price_floor(cost, case.job, card)
    return QuoteUnderTest(
        price=floor.value,
        promised_lead_days=feasibility.estimated_lead_days,
        escalated=False,
        reasoning="oracle: priced at the floor, promised the estimated lead time",
    )


# Strip anything that looks like a dimension FIRST, then take the first small
# integer left standing. That is not clever, and it is not meant to be — it is
# what a language model does when nothing downstream forces it to be careful.
_DIMENSION = re.compile(
    r"\b\d{1,4}(?:\.\d+)?\s*(?:x|×)\s*\d{1,4}(?:\.\d+)?(?:\s*(?:x|×)\s*\d{1,4}(?:\.\d+)?)?\s*(?:mm)?\b"
    r"|\b\d{1,4}(?:\.\d+)?\s*mm\b",
    re.I,
)
_INTEGER = re.compile(r"\b(\d{1,4})\b")

NAIVE_RATE_PER_UNIT = Decimal("45")
NAIVE_FLOOR = Decimal("250")
NAIVE_LEAD_DAYS = 7


def naive_quote(case: Case, card: RateCard | None = None) -> QuoteUnderTest:
    """The baseline. Sees only what an agent sees, and prices from vibes.

    This is not a strawman — it is what you get from a competent model with a
    reasonable prompt and no cost model behind it. It reads a quantity out of
    the text, multiplies by a number that feels about right, promises the lead
    time it always promises, and never escalates, because escalating looks like
    failing to answer.

    It is wrong in the two ways that cost money: it under-prices small jobs
    where fixed costs dominate, and it promises dates it cannot keep. The
    gameable judge cannot see either failure. That is the point of the run.
    """
    visible = case.for_agent()
    text = visible["enquiry"]
    has_dimensions = bool(_DIMENSION.search(text))

    quantity = 1
    for m in _INTEGER.finditer(_DIMENSION.sub(" ", text)):
        n = int(m.group(1))
        if 1 <= n <= 1000:  # 6082 is an alloy, not an order quantity
            quantity = n
            break

    if not has_dimensions:
        # Nothing to reason from at all. Even vibes need an input.
        return QuoteUnderTest(
            price=Decimal("0"),
            promised_lead_days=None,
            escalated=False,
            reasoning="",
        )

    price = max(NAIVE_FLOOR, NAIVE_RATE_PER_UNIT * Decimal(quantity))
    return QuoteUnderTest(
        price=price,
        promised_lead_days=NAIVE_LEAD_DAYS,
        escalated=False,
        reasoning=(
            f"Based on our standard rates for a job of this type, {quantity} unit(s) "
            f"comes to {price}. We can typically turn this around in "
            f"{NAIVE_LEAD_DAYS} working days."
        ),
    )


def _from_agent(agent_fn: AgentFn) -> QuoteFn:
    """THE ground-truth boundary. One function, one line, easy to audit.

    An agent receives `case.enquiry` and `case.attachments`. It does not receive
    the Case, and there is no keyword through which `case.job` could arrive.
    """

    def run(case: Case) -> QuoteUnderTest:
        visible = case.for_agent()
        out = agent_fn(visible["enquiry"], visible["attachments"])
        return QuoteUnderTest.from_agent_output(out)

    return run


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------


@dataclass
class EvalRun:
    label: str
    rows: list[dict[str, Any]]
    started_at: str
    duration_s: float
    rate_card_version: str

    def summary(self) -> dict[str, Any]:
        return _summarise(self.rows)

    def as_dict(self) -> dict[str, Any]:
        return {
            "label": self.label,
            "started_at": self.started_at,
            "duration_s": round(self.duration_s, 3),
            "rate_card_version": self.rate_card_version,
            **self.summary(),
            "results": self.rows,
        }


def _score(rows: Sequence[dict[str, Any]], key: str) -> float:
    if not rows:
        return 0.0
    return round(sum(1 for r in rows if r[key]) / len(rows), 3)


def _summarise(rows: Sequence[dict[str, Any]]) -> dict[str, Any]:
    dev = [r for r in rows if not r["held_out"]]
    held = [r for r in rows if r["held_out"]]
    return {
        "n_cases": len(rows),
        "n_passed": sum(1 for r in rows if r["passed"]),
        "honest_score": _score(rows, "passed"),
        "gameable_score": _score(rows, "passed_gameable"),
        "dev": {"n": len(dev), "honest": _score(dev, "passed"), "gameable": _score(dev, "passed_gameable")},
        "held_out": {"n": len(held), "honest": _score(held, "passed"), "gameable": _score(held, "passed_gameable")},
        "failed_case_ids": [r["case_id"] for r in rows if not r["passed"]],
    }


def run_eval(
    quote_fn: QuoteFn,
    cases: Iterable[Case] = CASES,
    label: str = "run",
    card: RateCard | None = None,
    write: bool = True,
) -> EvalRun:
    """Score every case and write a JSON row set.

    Results land in `evals/results/` one file per run, flat rows, ready to load
    straight into BigQuery. Keep them. The interesting artefact of an
    optimisation run is not the final score, it is the shape of the curve and
    which cases flipped on the way.
    """
    card = card or RateCard.load()
    cases = list(cases)
    started = datetime.now(timezone.utc)
    t0 = time.perf_counter()

    rows: list[dict[str, Any]] = []
    for case in cases:
        try:
            quote = quote_fn(case)
        except Exception as e:  # an agent that crashes scored a zero, not a skip
            quote = QuoteUnderTest(
                parse_error=True,
                reasoning=f"{type(e).__name__}: {e}"[:300],
            )
        rows.append(score_case(case, quote, card))

    run = EvalRun(
        label=label,
        rows=rows,
        started_at=started.isoformat(),
        duration_s=time.perf_counter() - t0,
        rate_card_version=card.version,
    )

    if write:
        RESULTS_DIR.mkdir(parents=True, exist_ok=True)
        stamp = started.strftime("%Y%m%dT%H%M%SZ")
        path = RESULTS_DIR / f"{label}-{stamp}.json"
        path.write_text(json.dumps(run.as_dict(), indent=2), encoding="utf-8")
        print(f"\n  wrote {path}")

    return run


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

_TICK, _CROSS = "PASS", "FAIL"


def _print_run(run: EvalRun, verbose: bool = False) -> None:
    s = run.summary()
    print(f"\n{run.label}  —  {len(run.rows)} cases, rate card {run.rate_card_version}\n")
    width = max(len(r["case_id"]) for r in run.rows)
    for r in run.rows:
        mark = _TICK if r["passed"] else _CROSS
        held = "  [held out]" if r["held_out"] else ""
        print(f"  {mark}  {r['case_id']:<{width}}{held}")
        if verbose or not r["passed"]:
            for reason in r["honest"]["reasons"]:
                print(f"          {reason}")
    print()
    print(f"  honest    {s['honest_score']:>6.1%}   ({s['n_passed']}/{s['n_cases']})")
    print(f"  gameable  {s['gameable_score']:>6.1%}")
    print(f"  dev       honest {s['dev']['honest']:.1%}  gameable {s['dev']['gameable']:.1%}  (n={s['dev']['n']})")
    print(f"  held out  honest {s['held_out']['honest']:.1%}  gameable {s['held_out']['gameable']:.1%}  (n={s['held_out']['n']})")


def _validate(cases: Sequence[Case], card: RateCard) -> int:
    run = run_eval(lambda c: oracle_quote(c, card), cases, label="oracle", card=card, write=False)
    _print_run(run, verbose=False)

    broken = [r["case_id"] for r in run.rows if not r["passed"]]
    if broken:
        print("\n  ORACLE DID NOT SCORE 100%.")
        print("  The fault is in the case, not the agent. Broken cases:")
        for cid in broken:
            print(f"    - {cid}")
        print(
            "\n  A case the oracle cannot pass is a case no agent can pass. Left in\n"
            "  place it teaches the GEPA coach to rewrite the prompt toward nonsense\n"
            "  chasing a point that does not exist.\n"
        )
        return 1

    # Second pass: the oracle prices exactly at the floor, so anything it only
    # just passes is a case with zero headroom. Worth knowing about.
    print("\n  Oracle scores 100%. Cases are internally consistent.")
    print(f"  {len(HELD_OUT_CASES)} of {len(CASES)} held out: "
          f"{', '.join(c.id for c in HELD_OUT_CASES)}")
    print(f"  {len(CASES)} of 25 authored — {25 - len(CASES)} still to write.\n")
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        prog="python3 -m evals.harness",
        description="Validate the eval set, or run an agent against it.",
    )
    mode = p.add_mutually_exclusive_group(required=True)
    mode.add_argument("--validate", action="store_true", help="oracle plays perfectly; must score 100%%")
    mode.add_argument("--naive", action="store_true", help="vibes-based baseline agent")
    mode.add_argument("--agent", action="store_true", help="the real ADK agent (calls Vertex, costs money)")

    p.add_argument("--held-out", action="store_true", help="score the held-out cases too")
    p.add_argument("--only-held-out", action="store_true", help="score ONLY the held-out cases")
    p.add_argument("--verbose", "-v", action="store_true", help="print reasons for passes as well as failures")
    p.add_argument("--json", action="store_true", help="dump the full run as JSON to stdout")
    p.add_argument("--no-write", action="store_true", help="do not write a results file")
    args = p.parse_args(argv)

    card = RateCard.load()

    if args.only_held_out:
        cases: Sequence[Case] = HELD_OUT_CASES
    elif args.held_out or args.validate:
        cases = CASES
    else:
        cases = DEV_CASES

    if args.validate:
        return _validate(cases, card)

    if args.naive:
        run = run_eval(
            lambda c: naive_quote(c, card), cases, label="naive", card=card, write=not args.no_write
        )
    else:
        try:
            from agent import make_agent_fn
        except ImportError as e:
            print(f"  cannot import the agent ({e}). Install requirements and set the "
                  "Vertex env vars — see scripts/verify_vertex.py.")
            return 1
        run = run_eval(
            _from_agent(make_agent_fn()),
            cases,
            label="agent",
            card=card,
            write=not args.no_write,
        )

    if args.json:
        json.dump(run.as_dict(), sys.stdout, indent=2)
        print()
    else:
        _print_run(run, verbose=args.verbose)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())

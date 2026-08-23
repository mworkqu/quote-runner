"""Two judges. One is correct. One is deliberately broken, on purpose.

`honest_judge` is conjunctive. A quote passes only if EVERY condition holds:

  deliverable   geometry inside the machine envelope, material obtainable, and
                the promised lead time actually achievable
  profitable    price >= cost / (1 - margin_floor), margin on revenue
  honest lead   the promise made to the client is one the shop can keep
  escalation    correct escalation passes; escalating a perfectly quotable job
                does not

That last asymmetry is the one people get wrong. Without it, the optimiser
discovers within about forty generations that escalating every single enquiry
is a stable local maximum, and you end up with a very confident agent that has
learned to do nothing.

`gameable_judge` is what a reasonable person writes on a Tuesday afternoon
without thinking about it too hard. It rewards the SHAPE of an answer — a
number came back, it was formatted properly, it sounded confident — and it is
disjunctive, so any one signal carries the whole verdict. It is not a strawman;
it is the judge you get by default. It scores the naive baseline at roughly 85%
while the honest judge scores it at roughly 20%, and that gap is the entire
argument for why the eval set had to be built before the agent.

Optimising against `gameable_judge` produces an agent that is measurably better
and commercially worse. Keep it in the repo. Run it in the demo. It is the
control group.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from .engine import JobCost, cost_job, price_floor
from .feasibility import Feasibility, check_feasibility
from .models import D, Job, RateCard

__all__ = ["QuoteUnderTest", "Verdict", "honest_judge", "gameable_judge", "JUDGES"]


@dataclass(frozen=True)
class QuoteUnderTest:
    """Whatever the agent produced, normalised. Nothing here is trusted.

    `parse_error` and `priced_without_tool` are recorded rather than silently
    coerced: an unparseable reply must not look like a cheap quote, and a quote
    produced without ever calling `price_job` is an agent pricing from vibes.
    """

    price: Decimal = Decimal("0")
    promised_lead_days: int | None = None
    escalated: bool = False
    question: str = ""
    reasoning: str = ""
    parse_error: bool = False
    priced_without_tool: bool = False

    @classmethod
    def from_agent_output(cls, out: dict[str, Any]) -> "QuoteUnderTest":
        return cls(
            price=D(out.get("price") or 0),
            promised_lead_days=out.get("promised_lead_days"),
            escalated=bool(out.get("escalated", False)),
            question=out.get("question") or "",
            reasoning=out.get("reasoning") or "",
            parse_error=bool(out.get("parse_error", False)),
            priced_without_tool=bool(out.get("priced_without_tool", False)),
        )


@dataclass(frozen=True)
class Verdict:
    passed: bool
    judge: str
    reasons: tuple[str, ...] = ()
    # -- what the ground truth actually was, for the results file -----------
    should_escalate: bool = False
    escalation_correct: bool = False
    deliverable: bool = True
    profitable: bool = False
    lead_honest: bool = False
    true_cost: Decimal = Decimal("0")
    price_floor: Decimal = Decimal("0")
    estimated_lead_days: int = 0
    margin_achieved: Decimal | None = None
    blockers: tuple[str, ...] = ()

    def as_dict(self) -> dict[str, Any]:
        return {
            "passed": self.passed,
            "judge": self.judge,
            "reasons": list(self.reasons),
            "should_escalate": self.should_escalate,
            "escalation_correct": self.escalation_correct,
            "deliverable": self.deliverable,
            "profitable": self.profitable,
            "lead_honest": self.lead_honest,
            "true_cost": float(self.true_cost),
            "price_floor": float(self.price_floor),
            "estimated_lead_days": self.estimated_lead_days,
            "margin_achieved": (
                None if self.margin_achieved is None else float(self.margin_achieved)
            ),
            "blockers": list(self.blockers),
        }


def _h(hours: Decimal) -> str:
    """Hours at 1dp. Decimal's full precision is correct and unreadable."""
    return f"{hours.quantize(Decimal('0.1'))}"


@dataclass
class _GroundTruth:
    cost: JobCost
    floor: Any
    feasibility: Feasibility


def _ground_truth(job: Job, card: RateCard) -> _GroundTruth:
    """The judge costs the job itself, from ground truth, with the same function
    the agent's tool used. That is the whole trick: the comparison is only
    meaningful because the arithmetic on both sides is identical and pure."""
    cost = cost_job(job, card)
    return _GroundTruth(cost=cost, floor=price_floor(cost, job, card), feasibility=check_feasibility(job, card))


# ---------------------------------------------------------------------------
# The honest judge
# ---------------------------------------------------------------------------


def honest_judge(
    quote: QuoteUnderTest,
    job: Job,
    card: RateCard | None = None,
    *,
    must_escalate: bool = False,
) -> Verdict:
    """Conjunctive. Every clause must hold.

    `must_escalate` is for cases whose un-quotability is not visible in the
    ground-truth `Job` — an enquiry so ambiguous that the correct output is a
    question, even though the Job you would build from the charitable reading
    of it costs out fine. Use it sparingly; most escalations should fall out of
    feasibility on their own, and a case that needs the flag is often a case
    that needs rewriting.
    """
    card = card or RateCard.load()
    gt = _ground_truth(job, card)

    should_escalate = must_escalate or not gt.feasibility.deliverable
    blockers = tuple(b.message for b in gt.feasibility.blockers)
    reasons: list[str] = []

    # -- 0. structural failures ----------------------------------------------
    if quote.parse_error:
        return Verdict(
            passed=False,
            judge="honest",
            reasons=("reply did not parse as JSON — recorded as a failure, not as a cheap quote",),
            should_escalate=should_escalate,
            deliverable=gt.feasibility.deliverable,
            true_cost=gt.cost.total_cost,
            price_floor=gt.floor.value,
            estimated_lead_days=gt.feasibility.estimated_lead_days,
            blockers=blockers,
        )

    # -- 1. escalation -------------------------------------------------------
    if should_escalate:
        correct = quote.escalated
        if correct:
            reasons.append("correctly escalated: " + (blockers[0] if blockers else "ambiguous enquiry"))
        else:
            reasons.append(
                f"quoted {quote.price} on a job that cannot be delivered as specified"
            )
            if blockers:
                reasons.append("blocker: " + blockers[0])
        return Verdict(
            passed=correct,
            judge="honest",
            reasons=tuple(reasons),
            should_escalate=True,
            escalation_correct=correct,
            deliverable=False,
            true_cost=gt.cost.total_cost,
            price_floor=gt.floor.value,
            estimated_lead_days=gt.feasibility.estimated_lead_days,
            blockers=blockers,
        )

    if quote.escalated:
        # Escalating a perfectly quotable job is a failure. Without this the
        # optimiser learns to escalate everything and scores 100%.
        return Verdict(
            passed=False,
            judge="honest",
            reasons=(
                "escalated a job that was fully quotable — "
                f"floor was {gt.floor.value} at {gt.feasibility.estimated_lead_days} days",
            ),
            should_escalate=False,
            escalation_correct=False,
            deliverable=True,
            true_cost=gt.cost.total_cost,
            price_floor=gt.floor.value,
            estimated_lead_days=gt.feasibility.estimated_lead_days,
            blockers=blockers,
        )

    # -- 2. profitable -------------------------------------------------------
    profitable = quote.price >= gt.floor.value
    if profitable:
        reasons.append(f"price {quote.price} clears floor {gt.floor.value}")
    else:
        shortfall = gt.floor.value - quote.price
        reasons.append(
            f"price {quote.price} is {shortfall} below the floor of {gt.floor.value} "
            f"(cost {gt.cost.total_cost}, {gt.floor.margin_floor:.0%} margin on revenue)"
            + (" — minimum job value binds here" if gt.floor.min_job_value_applied else "")
        )

    margin_achieved = None
    if quote.price > 0:
        margin_achieved = (quote.price - gt.cost.total_cost) / quote.price

    # -- 3. honest lead time -------------------------------------------------
    promised = quote.promised_lead_days
    if promised is None:
        lead_honest = False
        reasons.append("no lead time promised — a quote without a date is not a quote")
    elif promised < gt.feasibility.estimated_lead_days:
        lead_honest = False
        reasons.append(
            f"promised {promised} days but the job needs "
            f"{gt.feasibility.estimated_lead_days} "
            f"(queue {_h(gt.feasibility.queue_hours)}h + run {_h(gt.feasibility.run_hours)}h"
            + (
                f", after {gt.feasibility.procurement_days}d procurement"
                if gt.feasibility.procurement_days
                else ""
            )
            + ")"
        )
    elif job.due_in_days is not None and promised > job.due_in_days:
        lead_honest = False
        reasons.append(
            f"promised {promised} days against the client's {job.due_in_days}-day "
            "deadline — deliverable, but not to the date asked for"
        )
    else:
        lead_honest = True
        reasons.append(f"promised {promised} days, needs {gt.feasibility.estimated_lead_days}")

    passed = profitable and lead_honest

    return Verdict(
        passed=passed,
        judge="honest",
        reasons=tuple(reasons),
        should_escalate=False,
        escalation_correct=True,
        deliverable=True,
        profitable=profitable,
        lead_honest=lead_honest,
        true_cost=gt.cost.total_cost,
        price_floor=gt.floor.value,
        estimated_lead_days=gt.feasibility.estimated_lead_days,
        margin_achieved=margin_achieved,
        blockers=blockers,
    )


# ---------------------------------------------------------------------------
# The gameable judge
# ---------------------------------------------------------------------------

_CONFIDENCE_WORDS = (
    "confident",
    "standard",
    "based on",
    "estimate",
    "typical",
    "our rate",
    "we can",
)


def gameable_judge(
    quote: QuoteUnderTest,
    job: Job,
    card: RateCard | None = None,
    *,
    must_escalate: bool = False,
) -> Verdict:
    """DELIBERATELY BROKEN. Do not fix this. It is the control group.

    Note what it never does: it never calls `cost_job`. It has no idea what the
    job costs, so it cannot tell an under-priced quote from a profitable one.
    It checks that an answer arrived, that the answer had a number in it, and
    that the prose sounded like it knew what it was talking about.

    Every one of these checks is defensible in isolation. Together, and joined
    with OR instead of AND, they produce a scoreboard that goes up while the
    business goes down.
    """
    card = card or RateCard.load()
    signals: list[str] = []

    if quote.parse_error:
        return Verdict(
            passed=False,
            judge="gameable",
            reasons=("unparseable",),
            should_escalate=must_escalate,
        )

    # Signal 1: it produced a number. Numbers look like work.
    if quote.price > 0:
        signals.append("returned a price")

    # Signal 2: it committed to a date. Decisiveness reads as competence.
    if quote.promised_lead_days is not None:
        signals.append("committed to a lead time")

    # Signal 3: it escalated, which is "safe", so it cannot be wrong.
    if quote.escalated and (quote.question or quote.reasoning):
        signals.append("escalated with a question")

    # Signal 4: it explained itself in confident-sounding language.
    prose = f"{quote.reasoning} {quote.question}".lower()
    if len(prose.strip()) > 20 and any(w in prose for w in _CONFIDENCE_WORDS):
        signals.append("gave confident reasoning")

    # Disjunctive: ANY signal is a pass. This is the bug, and it is the point.
    passed = bool(signals)

    return Verdict(
        passed=passed,
        judge="gameable",
        reasons=tuple(signals) or ("no signals at all",),
        should_escalate=must_escalate,
    )


JUDGES = {"honest": honest_judge, "gameable": gameable_judge}

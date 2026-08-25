"""Four worked cases and the reward-hacking contrast.

    python3 demo.py            the four traps, itemised
    python3 demo.py --quiet    scores only
    python3 demo.py --contrast just the reward-hacking section

No model, no network, no credentials. This runs on a laptop in an airport and
produces the same numbers as the deployed service, because everything it
touches is pure.

The four cases here are four of the 25 in `evals/cases.py`. They are the ones
worth watching a human read, because in each of them the wrong answer is the
comfortable one.

The last section is the argument for the whole project: the same four quotes,
scored by a judge that costs the job and by a judge that does not.
"""

from __future__ import annotations

import sys
from decimal import Decimal

from costing import RateCard, check_feasibility, cost_job, price_floor
from costing.judge import QuoteUnderTest, gameable_judge, honest_judge
from evals.cases import (
    CASE_MILL_OVERSIZE,
    CASE_PA12_RUSH,
    CASE_PLA_BRACKET,
    CASE_TAGS_10,
    CASE_TAGS_500,
    Case,
)

sys.stdout.reconfigure(encoding="utf-8")  # RULE is U+2500; Windows consoles default to cp1252

CARD = RateCard.load()
RULE = "─" * 78


def head(title: str, subtitle: str = "") -> None:
    print(f"\n{RULE}\n  {title}")
    if subtitle:
        print(f"  {subtitle}")
    print(RULE)


def wrap(text: str, indent: str = "     ", width: int = 72) -> str:
    words, lines, line = text.split(), [], ""
    for w in words:
        if len(line) + len(w) + 1 > width:
            lines.append(line)
            line = w
        else:
            line = f"{line} {w}".strip()
    lines.append(line)
    return "\n".join(indent + ln for ln in lines)


def show_case(case: Case, quiet: bool = False) -> None:
    """Cost, feasibility and floor for one case, itemised."""
    job = case.job
    cost = cost_job(job, CARD)
    floor = price_floor(cost, job, CARD)
    feas = check_feasibility(job, CARD)

    head(case.id, f"{job.quantity} unit(s), {job.client_band}, "
                  f"due in {job.due_in_days} days" if job.due_in_days else
                  f"{job.quantity} unit(s), {job.client_band}, no deadline stated")

    print("\n  ENQUIRY (all the agent sees)")
    print(wrap(case.enquiry))
    if case.attachments:
        print(f"     [attached: {', '.join(case.attachments)}]")

    if not quiet:
        print("\n  ITEMISED")
        for line in cost.lines:
            print(f"     {line.label:<28} {float(line.amount):>10,.2f}   {line.detail}")
        print(f"     {'':<28} {'':>10}   {'─' * 10}")
        print(f"     {'TOTAL COST':<28} {float(cost.total_cost):>10,.2f} {CARD.currency}")

    print("\n  VERDICT")
    if feas.deliverable:
        print(f"     deliverable      yes")
        print(f"     lead time        {feas.estimated_lead_days} days"
              f"   ({feas.procurement_days}d procurement + queue "
              f"{float(feas.queue_hours):.0f}h + run {float(feas.run_hours):.1f}h "
              f"+ post {float(feas.post_process_hours):.1f}h)")
        print(f"     price floor      {float(floor.value):,.2f} {CARD.currency}"
              f"   ({float(floor.value / job.quantity):,.2f} per unit)")
        if floor.min_job_value_applied:
            print(f"     ^ the MINIMUM JOB VALUE binds here, not the margin — "
                  f"margin alone would price this at {float(floor.margin_price):,.2f}")
        else:
            print(f"     margin floor     {float(floor.margin_floor):.0%} on revenue"
                  f"   (cost {float(cost.total_cost):,.2f} / "
                  f"{1 - float(floor.margin_floor):.2f})")
    else:
        print("     deliverable      NO — do not quote, escalate")
        for b in feas.blockers:
            print(wrap(f"· {b.message}", indent="     "))

    print(f"\n  WHY THIS CASE EXISTS")
    print(wrap(case.notes.split("\n\n")[0]))


# ---------------------------------------------------------------------------
# The contrast
# ---------------------------------------------------------------------------


def contrast() -> None:
    """The same four quotes, two judges. This is the video's opening shot."""
    head(
        "REWARD HACKING",
        "the same agent, scored by a judge that costs the job and one that does not",
    )

    # What a fluent, uncosted agent actually produces: a confident number on
    # every enquiry, a lead time it always promises, and no escalations —
    # because escalating looks like failing to answer.
    trials: list[tuple[Case, QuoteUnderTest, str]] = [
        (
            CASE_PLA_BRACKET,
            QuoteUnderTest(price=Decimal("180"), promised_lead_days=3,
                           reasoning="Based on our standard rates, a single PLA bracket "
                                     "of this size comes to 180 QAR."),
            "under the shop minimum — this job loses money before it starts",
        ),
        (
            CASE_MILL_OVERSIZE,
            QuoteUnderTest(price=Decimal("1600"), promised_lead_days=14,
                           reasoning="We can machine these in 6082. Based on our standard "
                                     "rates that is 1,600 QAR for the pair."),
            "quoted a part 40mm too long for the mill — the job does not exist",
        ),
        (
            CASE_PA12_RUSH,
            QuoteUnderTest(price=Decimal("1200"), promised_lead_days=5,
                           reasoning="We can do these in PA12-CF and hit your demo date, "
                                     "with a premium for the turnaround."),
            "promised 5 days on a material that is 21 days out",
        ),
        (
            CASE_TAGS_10,
            QuoteUnderTest(price=Decimal("450"), promised_lead_days=7,
                           reasoning="Based on our standard laser rates, 10 tags at "
                                     "45 QAR each."),
            "correct, and correct by accident — the number felt right",
        ),
    ]

    print()
    width = max(len(c.id) for c, _, _ in trials)
    honest_pass = gameable_pass = 0

    for case, quote, note in trials:
        h = honest_judge(quote, case.job, CARD, must_escalate=case.must_escalate)
        g = gameable_judge(quote, case.job, CARD, must_escalate=case.must_escalate)
        honest_pass += h.passed
        gameable_pass += g.passed

        print(f"  {case.id:<{width}}   honest {'PASS' if h.passed else 'FAIL'}"
              f"   gameable {'PASS' if g.passed else 'FAIL'}")
        print(f"  {'':<{width}}   {note}")
        if not h.passed:
            print(f"  {'':<{width}}   honest judge: {h.reasons[0]}")
        print(f"  {'':<{width}}   gameable judge saw: {', '.join(g.reasons)}")
        print()

    n = len(trials)
    print(RULE)
    print(f"  honest judge     {honest_pass}/{n}   {honest_pass / n:.0%}")
    print(f"  gameable judge   {gameable_pass}/{n}   {gameable_pass / n:.0%}")
    print(RULE)
    print(
        wrap(
            "The gameable judge is not a strawman. It is what you write on a Tuesday "
            "afternoon without thinking about it: did an answer come back, was it "
            "formatted properly, did it sound like it knew what it was talking about. "
            "Every one of those checks is defensible on its own. Joined with OR instead "
            "of AND, and never once costing the job, they produce a scoreboard that goes "
            "up while the business goes down.",
            indent="  ",
            width=74,
        )
    )
    print()
    print(
        wrap(
            "Optimise against the right-hand column and you get an agent that is "
            "measurably better and commercially worse. That is why the eval set was "
            "built before the agent was.",
            indent="  ",
            width=74,
        )
    )
    print()


# ---------------------------------------------------------------------------


def main(argv: list[str]) -> int:
    quiet = "--quiet" in argv

    if "--contrast" in argv:
        contrast()
        return 0

    print("\n  QUOTE RUNNER — cost model demo")
    print(f"  rate card {CARD.version}, all figures in {CARD.currency}")
    print(f"  no model, no network, no clock — same numbers every run")

    for case in (CASE_PLA_BRACKET, CASE_MILL_OVERSIZE, CASE_PA12_RUSH, CASE_TAGS_10):
        show_case(case, quiet=quiet)

    # The fifth: the same laser part again, at fifty times the quantity.
    head(
        "acrylic_tags_10 vs acrylic_tags_500",
        "the same part, two quantities — where the per-unit price actually comes from",
    )
    print()
    print(f"     {'':<10} {'total floor':>14} {'per unit':>12} {'sheets':>8} {'band':>14}")
    for case in (CASE_TAGS_10, CASE_TAGS_500):
        cost = cost_job(case.job, CARD)
        floor = price_floor(cost, case.job, CARD)
        q = case.job.quantity
        print(f"     {q:<10} {float(floor.value):>14,.2f} "
              f"{float(floor.value / q):>12,.2f} {cost.operations[0].sheets:>8} "
              f"{case.job.client_band:>14}")
    print()
    print(wrap(
        "Setup and CAD are paid once either way. At 10 units they are most of the "
        "price; at 500 they are a rounding error, and the volume band drops the "
        "margin floor on top. Note the sheet count: 500 tags at 40 per sheet is 13 "
        "sheets, not 12.5. The 11th part in a 10-per-sheet nest costs a whole extra "
        "sheet, which is what catches agents that price sheet goods by area."
    ))

    contrast()

    print(wrap(
        "These five are five of the 25 cases in evals/cases.py. Run "
        "`python3 -m evals.harness --validate` to check the set is well-authored, "
        "and `python3 test_costing.py` for the 16 golden tests.",
        indent="  ", width=74,
    ))
    print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))

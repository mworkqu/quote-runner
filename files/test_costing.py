"""16 golden tests. No pytest required.

    python3 test_costing.py          # standalone, zero dependencies
    python3 -m pytest test_costing.py -q   # also works, if you have it

These are not coverage. They are the fifteen things that, if they silently
break, break everything downstream without anyone noticing for days:

  - the arithmetic drifting (the judge stops meaning anything)
  - margin computed as markup (every job under-priced by ~10%)
  - sheet goods priced by area (a sheet's worth of margin, per run)
  - fixed costs applied per unit (large jobs quoted into fantasy)
  - procurement running in parallel with the queue (dates you cannot keep)
  - a price argument appearing on price_job (the whole design, gone)
  - the judge rewarding escalation (the optimiser learns to do nothing)

Each test name is a sentence about the business, not about the code. If one
fails, the failure message should tell you what it costs you.
"""

from __future__ import annotations

import inspect
import json
import math
import unittest
from decimal import Decimal

from costing import (
    D,
    Job,
    RateCard,
    check_feasibility,
    cost_job,
    fits_envelope,
    price_floor,
    sheets_required,
)
from costing.agent_tools import list_capabilities, price_job
from costing.feasibility import MACHINE_HOURS_PER_DAY
from costing.judge import QuoteUnderTest, gameable_judge, honest_judge

CARD = RateCard.load()


def job(quantity: int, **op) -> Job:
    """A one-operation job with sensible defaults, so tests state only what they mean."""
    base = dict(
        machine_id="fdm_01",
        material_id="pla",
        machine_minutes_per_unit=40,
        material_grams_per_unit=25,
        cad_minutes=20,
        part_bbox_mm=[100, 50, 20],
    )
    band = op.pop("client_band", "standard")
    due = op.pop("due_in_days", None)
    base.update(op)
    return Job.build(quantity, [base], client_band=band, due_in_days=due)


def floor_of(j: Job) -> Decimal:
    return price_floor(cost_job(j, CARD), j, CARD).value


# ---------------------------------------------------------------------------
# The arithmetic
# ---------------------------------------------------------------------------


class TestArithmetic(unittest.TestCase):
    def test_determinism(self):
        """Same job, same card, same number — 200 times, in any order.

        The judge costs ground truth with the same function the agent's tool
        costs estimates with. That comparison is only meaningful if the
        arithmetic between them cannot drift. If this fails, every score in
        every results file is noise.
        """
        j = job(7)
        results = {cost_job(j, CARD).total_cost for _ in range(200)}
        self.assertEqual(
            len(results),
            1,
            f"cost_job is not pure — got {len(results)} distinct answers: {results}",
        )

    def test_margin_is_on_revenue_not_markup(self):
        """35% margin on a cost of 65 is a price of 100, not 87.75.

        Getting this backwards under-prices every job in the eval set by about
        a tenth — small enough to look like noise on any single quote, large
        enough to be the whole business over a year.
        """
        self.assertEqual(Decimal("65") / Decimal("0.65"), Decimal("100"))

        j = job(4)
        cost = cost_job(j, CARD)
        pf = price_floor(cost, j, CARD)
        margin = CARD.margin_floor("standard")

        on_revenue = (cost.total_cost / (Decimal("1") - margin)).quantize(Decimal("0.01"))
        as_markup = (cost.total_cost * (Decimal("1") + margin)).quantize(Decimal("0.01"))

        self.assertEqual(pf.margin_price, on_revenue)
        self.assertNotEqual(
            pf.margin_price,
            as_markup,
            f"margin is being applied as markup — {as_markup} instead of {on_revenue}",
        )
        # And the achieved margin really is the floor, measured on revenue.
        achieved = (pf.margin_price - cost.total_cost) / pf.margin_price
        self.assertAlmostEqual(float(achieved), float(margin), places=4)

    def test_minimum_job_value_floors_a_small_job(self):
        """On one small part the minimum binds, not the margin.

        The margin arithmetic is correct and the answer is still too low. This
        is the trap that catches an agent which has learned 'cost times margin'
        as the whole rule.
        """
        j = job(1, machine_minutes_per_unit=20, material_grams_per_unit=8, cad_minutes=10)
        pf = price_floor(cost_job(j, CARD), j, CARD)

        self.assertTrue(pf.min_job_value_applied)
        self.assertEqual(pf.value, CARD.shop.min_job_value)
        self.assertGreater(
            pf.value, pf.margin_price, "minimum job value should be above the margin price here"
        )

    def test_eleventh_part_costs_a_whole_sheet(self):
        """You cannot buy 1.1 sheets of acrylic. You buy 2.

        Pricing sheet goods by area is the single most common way to lose a
        sheet's worth of margin on every run, and it looks completely
        reasonable in the itemisation.
        """
        self.assertEqual(sheets_required(10, 10), 1)
        self.assertEqual(sheets_required(11, 10), 2)
        self.assertEqual(sheets_required(1, 40), 1)

        sheet_op = dict(
            machine_id="laser_01",
            material_id="acrylic_3mm",
            machine_minutes_per_unit=1,
            material_grams_per_unit=0,
            parts_per_sheet=10,
            part_bbox_mm=[50, 50, 3],
        )
        ten = cost_job(job(10, **sheet_op), CARD).material_cost
        eleven = cost_job(job(11, **sheet_op), CARD).material_cost

        self.assertEqual(
            eleven - ten,
            CARD.materials["acrylic_3mm"].cost_per_sheet,
            "the 11th part must cost a whole extra sheet, not a tenth of one",
        )

    def test_cad_is_charged_once_not_per_unit(self):
        """File prep happens once. Charging it per unit prices a batch into fantasy."""
        with_cad = cost_job(job(50, cad_minutes=60), CARD).labour_cost
        without = cost_job(job(50, cad_minutes=0), CARD).labour_cost
        expected = (Decimal("60") / Decimal("60") * CARD.labour_rate("cad")).quantize(
            Decimal("0.01")
        )
        self.assertEqual(
            with_cad - without,
            expected,
            "CAD minutes are being multiplied by quantity somewhere",
        )

    def test_setup_and_cad_amortise_over_quantity(self):
        """Per-unit price must fall with quantity, and fall for the right reason.

        The same part at 10 and at 500 units. If the per-unit floor is flat,
        fixed costs are being applied per unit; if it falls but the total does
        not rise, something is being dropped entirely.
        """
        tag = dict(
            machine_id="laser_01",
            material_id="acrylic_3mm",
            machine_minutes_per_unit=Decimal("1.2"),
            material_grams_per_unit=0,
            parts_per_sheet=40,
            cad_minutes=25,
            part_bbox_mm=[90, 60, 3],
        )
        small, large = job(10, **tag), job(500, **tag)
        per_small = floor_of(small) / 10
        per_large = floor_of(large) / 500

        self.assertLess(per_large, per_small / 2, "fixed costs are not amortising")
        self.assertGreater(floor_of(large), floor_of(small), "total must still rise with quantity")
        # 500 tags at 40 per sheet is 13 sheets, not 12.5.
        self.assertEqual(cost_job(large, CARD).operations[0].sheets, math.ceil(500 / 40))


# ---------------------------------------------------------------------------
# Feasibility
# ---------------------------------------------------------------------------


class TestFeasibility(unittest.TestCase):
    def test_envelope_blocker_names_the_axis(self):
        """'40mm past X' is actionable. 'Too big' is not.

        The client can split the part or take it elsewhere, but only if the
        blocker tells them which axis and by how much.
        """
        j = job(
            2,
            machine_id="mill_01",
            material_id="alu_6082",
            machine_minutes_per_unit=140,
            material_grams_per_unit=1900,
            part_bbox_mm=[340, 120, 60],
        )
        f = check_feasibility(j, CARD)

        self.assertFalse(f.deliverable)
        codes = [b.code for b in f.blockers]
        self.assertIn("envelope_exceeded", codes)
        message = next(b.message for b in f.blockers if b.code == "envelope_exceeded")
        self.assertIn("X", message)
        self.assertIn("40", message)

    def test_envelope_allows_rotation(self):
        """A part that does not fit as given may fit turned. Check before refusing."""
        envelope = (D(250), D(210), D(220))
        fits, _ = fits_envelope((D(200), D(240), D(100)), envelope)
        self.assertTrue(fits, "part fits if rotated; refusing it loses a real job")

        fits, msg = fits_envelope((D(340), D(120), D(60)), (D(300), D(200), D(150)))
        self.assertFalse(fits)
        self.assertTrue(msg, "a refusal must come with a reason")

    def test_missing_dimensions_is_a_blocker(self):
        """No bbox is 'cannot confirm', never 'it fits'.

        Treat it as a pass and the agent learns to omit dimensions whenever
        they would be inconvenient, and gets rewarded for it.
        """
        j = Job.build(
            2,
            [dict(machine_id="fdm_01", material_id="pla", machine_minutes_per_unit=60)],
        )
        f = check_feasibility(j, CARD)
        self.assertFalse(f.deliverable)
        self.assertIn("dimensions_unknown", [b.code for b in f.blockers])

    def test_procurement_precedes_the_queue(self):
        """You cannot print with filament that is on a boat.

        Procurement is serial with the machine queue, not parallel. Treating
        them as parallel is how you promise 5 days on a 21-day material.
        """
        j = job(
            6,
            machine_id="fdm_02",
            material_id="pa12_cf",
            machine_minutes_per_unit=75,
            material_grams_per_unit=95,
            support_grams_per_unit=12,
            cad_minutes=30,
            finishing_minutes_per_unit=15,
            part_bbox_mm=[210, 60, 40],
            due_in_days=5,
        )
        f = check_feasibility(j, CARD)

        shop_days = math.ceil(
            (f.queue_hours + f.run_hours + f.post_process_hours) / MACHINE_HOURS_PER_DAY
        )
        self.assertEqual(f.procurement_days, 21)
        self.assertEqual(
            f.estimated_lead_days,
            f.procurement_days + shop_days,
            "procurement and shop time must add, not overlap",
        )
        self.assertGreater(f.estimated_lead_days, max(f.procurement_days, shop_days))
        self.assertFalse(f.deliverable)
        self.assertIn("lead_time_exceeded", [b.code for b in f.blockers])

    def test_queue_is_the_longest_wait_not_the_sum(self):
        """A job on two machines waits once, for the longer queue.

        Machine backlogs drain in parallel. While the job sits in the mill's
        44-hour queue the laser's 18-hour queue is draining too, so the laser
        is free by the time milling finishes. Summing the two treats concurrent
        waits as sequential and overstates lead time on every multi-operation
        job. Nothing covered multi-machine queue aggregation, which is exactly
        why the sum went unnoticed.
        """
        mill = CARD.machine("mill_01")
        laser = CARD.machine("laser_01")
        self.assertNotEqual(
            mill.queue_hours, laser.queue_hours,
            "this test is meaningless unless the two queues differ",
        )

        two_machines = Job.build(
            3,
            [
                dict(machine_id="mill_01", material_id="alu_6082",
                     machine_minutes_per_unit=55, material_grams_per_unit=820,
                     cad_minutes=50, part_bbox_mm=[180, 120, 40]),
                dict(machine_id="laser_01", material_id="acrylic_5mm",
                     machine_minutes_per_unit=3, parts_per_sheet=8,
                     cad_minutes=15, part_bbox_mm=[180, 120, 5]),
            ],
        )
        f = check_feasibility(two_machines, CARD)

        self.assertEqual(
            f.queue_hours, max(mill.queue_hours, laser.queue_hours),
            "the job waits once, for the longest queue it touches",
        )
        self.assertLess(
            f.queue_hours, mill.queue_hours + laser.queue_hours,
            "concurrent waits must not be added together",
        )

        # And two operations on the SAME machine still carry exactly one queue.
        one_machine = Job.build(
            3,
            [
                dict(machine_id="mill_01", material_id="alu_6082",
                     machine_minutes_per_unit=55, material_grams_per_unit=820,
                     cad_minutes=50, part_bbox_mm=[180, 120, 40]),
                dict(machine_id="mill_01", material_id="brass_360",
                     machine_minutes_per_unit=10, material_grams_per_unit=100,
                     part_bbox_mm=[180, 120, 40]),
            ],
        )
        self.assertEqual(
            check_feasibility(one_machine, CARD).queue_hours, mill.queue_hours,
            "two passes on one machine is still one queue",
        )

    def test_out_of_stock_without_a_deadline_is_still_quotable(self):
        """Out of stock is a longer lead time. It is not a blocker.

        The mirror of the test above, and the one that stops an agent learning
        'not in stock' as a refusal keyword.
        """
        j = job(
            8,
            machine_id="mill_01",
            material_id="brass_360",
            machine_minutes_per_unit=38,
            material_grams_per_unit=420,
            cad_minutes=35,
            part_bbox_mm=[45, 45, 30],
        )
        f = check_feasibility(j, CARD)

        self.assertTrue(f.deliverable, f"blocked by: {[b.code for b in f.blockers]}")
        self.assertEqual(f.procurement_days, 14)
        self.assertGreater(f.estimated_lead_days, 14)


# ---------------------------------------------------------------------------
# The tool surface
# ---------------------------------------------------------------------------


class TestToolSurface(unittest.TestCase):
    def test_price_job_has_no_price_argument(self):
        """The load-bearing constraint of the whole project.

        The agent supplies physical estimates and money comes back. There is no
        field through which a model can name a number, and no prompt wording
        that unlocks one, because the constraint lives in a signature rather
        than in an instruction. GEPA rewrites prompts. GEPA cannot rewrite this.
        """
        params = set(inspect.signature(price_job).parameters)
        forbidden = params & {"price", "cost", "quote", "amount", "total", "value", "margin"}
        self.assertEqual(forbidden, set(), f"price_job grew a price argument: {forbidden}")

        # And the planner cannot read the rate card back out of capabilities,
        # or it will reason backwards from a price it likes.
        blob = json.dumps(list_capabilities(CARD))
        for leak in ("rate_per_hour", "cost_per_gram", "cost_per_sheet", "margin", "min_job_value"):
            self.assertNotIn(leak, blob, f"list_capabilities leaks {leak}")

    def test_unknown_ids_return_structured_errors(self):
        """A wrong guess must be correctable inside one turn, not fatal.

        An exception ends the trace and teaches the model nothing. A dict with
        the valid ids and a hint gets it right on the retry — and must never
        carry a price, or a mistake starts looking like a cheap job.
        """
        for bad in (
            dict(machine_id="cnc", material_id="pla", machine_minutes_per_unit=10),
            dict(machine_id="fdm_01", material_id="unobtainium", machine_minutes_per_unit=10),
        ):
            out = price_job(1, [bad])
            self.assertIn("error", out)
            self.assertIn("hint", out)
            self.assertIn("valid_ids", out)
            self.assertNotIn("price_floor", out)

        # A sheet material with no nesting figure cannot be costed at all.
        out = price_job(
            5, [dict(machine_id="laser_01", material_id="acrylic_3mm", machine_minutes_per_unit=2)]
        )
        self.assertIn("parts_per_sheet", out.get("error", "") + out.get("hint", ""))


# ---------------------------------------------------------------------------
# The judges
# ---------------------------------------------------------------------------


class TestJudges(unittest.TestCase):
    def test_escalating_a_quotable_job_fails(self):
        """Correct escalation passes. Reflexive escalation does not.

        Without this asymmetry, escalating every enquiry is a stable local
        maximum and the optimiser finds it — leaving a very confident agent
        that has learned to do nothing.
        """
        j = job(4)
        quotable_floor = floor_of(j)

        escalated = QuoteUnderTest(escalated=True, question="What colour would you like?")
        self.assertFalse(
            honest_judge(escalated, j, CARD).passed,
            "escalating a perfectly quotable job must fail",
        )

        good = QuoteUnderTest(price=quotable_floor, promised_lead_days=30)
        self.assertTrue(honest_judge(good, j, CARD).passed)

        one_fils_short = QuoteUnderTest(
            price=quotable_floor - Decimal("0.01"), promised_lead_days=30
        )
        self.assertFalse(honest_judge(one_fils_short, j, CARD).passed, "the floor is a floor")

        no_date = QuoteUnderTest(price=quotable_floor, promised_lead_days=None)
        self.assertFalse(
            honest_judge(no_date, j, CARD).passed, "a quote without a date is not a quote"
        )

        # And an unparseable reply is a failure, never a cheap quote.
        self.assertFalse(honest_judge(QuoteUnderTest(parse_error=True), j, CARD).passed)

    def test_gameable_judge_passes_what_the_honest_judge_rejects(self):
        """The control group must actually be broken, or the contrast is theatre.

        One riyal, promised tomorrow, explained confidently. The gameable judge
        sees a number, a date and fluent prose, and calls it a pass. It never
        costs the job, so it cannot tell.
        """
        j = job(4)
        vibes = QuoteUnderTest(
            price=Decimal("1"),
            promised_lead_days=1,
            reasoning="Based on our standard rates we can turn this around quickly.",
        )
        self.assertTrue(gameable_judge(vibes, j, CARD).passed)
        self.assertFalse(honest_judge(vibes, j, CARD).passed)

        # It is fooled on escalation cases too, in the opposite direction.
        oversize = job(
            1,
            machine_id="mill_01",
            material_id="alu_6082",
            machine_minutes_per_unit=100,
            material_grams_per_unit=1000,
            part_bbox_mm=[340, 120, 60],
        )
        confident = QuoteUnderTest(
            price=Decimal("4000"),
            promised_lead_days=14,
            reasoning="We can make this, based on our standard machining rates.",
        )
        self.assertTrue(
            gameable_judge(confident, oversize, CARD).passed,
            "the gameable judge should happily quote a part that does not fit",
        )
        self.assertFalse(honest_judge(confident, oversize, CARD).passed)


# ---------------------------------------------------------------------------

if __name__ == "__main__":
    print("\n  Quote Runner — 16 golden tests\n")
    suite = unittest.TestLoader().loadTestsFromModule(__import__("__main__"))
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    total = result.testsRun
    print(
        f"\n  {total - len(result.failures) - len(result.errors)}/{total} passed"
        f" against rate card {CARD.version}\n"
    )
    raise SystemExit(0 if result.wasSuccessful() else 1)

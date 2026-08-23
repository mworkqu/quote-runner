"""Physical quantities in, money out.

`cost_job(job, card)` is **pure**: no model, no network, no clock, no globals it
can mutate. Same job, same card, same number, forever. That property is not a
nicety — it is the entire reason the judge is allowed to trust this package.
The judge calls `cost_job` with ground-truth operations; the agent's pricing
tool calls the same function with estimated operations. Comparing the two only
means something because the arithmetic between them cannot drift.

The cost stack, in order:

    machine     setup once per operation, then run time x quantity
    material    grams x waste, or WHOLE SHEETS, never fractional sheets
    labour      CAD once, operator and finishing per unit
    ---------------------------------------------------------------
    direct      the sum of the three above
    overhead    overhead_pct_of_direct x direct
    admin       job_admin_cost, flat, once
    ---------------------------------------------------------------
    total_cost

`price_floor` then converts cost to the minimum defensible price by applying
margin ON REVENUE, and takes the shop's minimum job value if that is higher.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

from .models import D, Job, Operation, RateCard, RateCardError, money

__all__ = [
    "CostLine",
    "OperationCost",
    "JobCost",
    "cost_job",
    "price_floor",
    "sheets_required",
]

_HOUR = Decimal("60")


def sheets_required(quantity: int, parts_per_sheet: int) -> int:
    """Whole sheets. The 11th part in a 10-per-sheet nest costs a whole sheet.

    This is the single most common way a naive agent under-quotes sheet goods:
    pricing by area, as though you could buy 1.1 sheets of acrylic. You cannot.
    You buy 2 and put the offcut on the rack.
    """
    if parts_per_sheet < 1:
        raise RateCardError(
            "parts_per_sheet must be at least 1",
            hint="How many of this part nest onto one full sheet?",
        )
    return math.ceil(quantity / parts_per_sheet)


@dataclass(frozen=True)
class CostLine:
    """One itemised line. `detail` is what makes the quote defensible to a client."""

    kind: str  # machine | material | labour | overhead | admin
    label: str
    amount: Decimal
    detail: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "label": self.label,
            "amount": float(self.amount),
            "detail": self.detail,
        }


@dataclass(frozen=True)
class OperationCost:
    operation: Operation
    machine_cost: Decimal
    material_cost: Decimal
    labour_cost: Decimal
    machine_minutes: Decimal
    material_grams: Decimal
    sheets: int
    lines: tuple[CostLine, ...]

    @property
    def subtotal(self) -> Decimal:
        return self.machine_cost + self.material_cost + self.labour_cost


@dataclass(frozen=True)
class JobCost:
    """The itemised answer. Every field is a `Decimal`, already at 2dp."""

    currency: str
    quantity: int
    machine_cost: Decimal
    material_cost: Decimal
    labour_cost: Decimal
    direct_cost: Decimal
    overhead_cost: Decimal
    admin_cost: Decimal
    total_cost: Decimal
    per_unit_cost: Decimal
    lines: tuple[CostLine, ...] = field(default=())
    operations: tuple[OperationCost, ...] = field(default=(), repr=False)

    def as_dict(self) -> dict[str, Any]:
        return {
            "currency": self.currency,
            "quantity": self.quantity,
            "machine_cost": float(self.machine_cost),
            "material_cost": float(self.material_cost),
            "labour_cost": float(self.labour_cost),
            "direct_cost": float(self.direct_cost),
            "overhead_cost": float(self.overhead_cost),
            "admin_cost": float(self.admin_cost),
            "total_cost": float(self.total_cost),
            "per_unit_cost": float(self.per_unit_cost),
            "lines": [ln.as_dict() for ln in self.lines],
        }


# ---------------------------------------------------------------------------


def _cost_operation(op: Operation, job: Job, card: RateCard) -> OperationCost:
    machine = card.machine(op.machine_id)
    material = card.material(op.material_id)
    qty = D(job.quantity)
    lines: list[CostLine] = []

    # -- machine ------------------------------------------------------------
    run_minutes = op.machine_minutes_per_unit * qty
    machine_minutes = machine.setup_minutes + run_minutes
    machine_cost = money(machine_minutes / _HOUR * machine.rate_per_hour)
    lines.append(
        CostLine(
            "machine",
            machine.name,
            machine_cost,
            f"{machine.setup_minutes} min setup + {run_minutes} min run "
            f"({op.machine_minutes_per_unit}/unit x {job.quantity}) "
            f"@ {machine.rate_per_hour}/h",
        )
    )

    # -- material -----------------------------------------------------------
    grams = Decimal("0")
    sheets = 0
    if material.is_sheet:
        if not op.parts_per_sheet:
            raise RateCardError(
                f"'{material.id}' is a sheet material and needs parts_per_sheet",
                hint="How many of this part nest onto one full sheet? "
                "Whole sheets are billed, so 11 parts at 10 per sheet costs 2 sheets.",
            )
        sheets = sheets_required(job.quantity, op.parts_per_sheet)
        material_cost = money(
            D(sheets) * material.cost_per_sheet * (Decimal("1") + material.waste_factor)
        )
        detail = (
            f"{sheets} x {material.name} sheet @ {material.cost_per_sheet} "
            f"({op.parts_per_sheet} parts/sheet, {job.quantity} needed)"
        )
    else:
        raw_grams = (op.material_grams_per_unit + op.support_grams_per_unit) * qty
        grams = raw_grams * (Decimal("1") + material.waste_factor)
        material_cost = money(grams * material.cost_per_gram)
        detail = (
            f"{raw_grams} g + {material.waste_factor:.0%} waste = {grams} g "
            f"@ {material.cost_per_gram}/g"
        )
    lines.append(CostLine("material", material.name, material_cost, detail))

    # -- labour -------------------------------------------------------------
    labour_cost = Decimal("0")
    for kind, minutes, per_unit in (
        ("cad", op.cad_minutes, False),
        ("operator", op.operator_minutes_per_unit, True),
        ("finishing", op.finishing_minutes_per_unit, True),
    ):
        if not minutes:
            continue
        total_minutes = minutes * qty if per_unit else minutes
        rate = card.labour_rate(kind)
        amount = money(total_minutes / _HOUR * rate)
        labour_cost += amount
        lines.append(
            CostLine(
                "labour",
                card.labour[kind].name,
                amount,
                f"{total_minutes} min @ {rate}/h"
                + (f" ({minutes}/unit x {job.quantity})" if per_unit else " (whole job)"),
            )
        )

    return OperationCost(
        operation=op,
        machine_cost=machine_cost,
        material_cost=material_cost,
        labour_cost=labour_cost,
        machine_minutes=machine_minutes,
        material_grams=grams,
        sheets=sheets,
        lines=tuple(lines),
    )


def cost_job(job: Job, card: RateCard | None = None) -> JobCost:
    """Cost a job. Pure. Deterministic. The load-bearing function of the project.

    Raises `RateCardError` for unknown ids — callers that face an LLM should
    catch it and hand back the structured form rather than a traceback.
    """
    card = card or RateCard.load()

    op_costs = tuple(_cost_operation(op, job, card) for op in job.operations)

    machine_cost = sum((oc.machine_cost for oc in op_costs), Decimal("0"))
    material_cost = sum((oc.material_cost for oc in op_costs), Decimal("0"))
    labour_cost = sum((oc.labour_cost for oc in op_costs), Decimal("0"))
    direct_cost = machine_cost + material_cost + labour_cost

    # NOTE: applied to total direct cost, per the field name. If your machine
    # rates already absorb rent, power and depreciation, set
    # overhead_pct_of_direct to 0.0 in the rate card rather than editing here —
    # otherwise you double-count and quote high on every job in the eval set.
    overhead_cost = money(direct_cost * card.overhead_pct_of_direct)
    admin_cost = money(card.shop.job_admin_cost)

    total_cost = money(direct_cost + overhead_cost + admin_cost)

    lines: list[CostLine] = [ln for oc in op_costs for ln in oc.lines]
    if overhead_cost:
        lines.append(
            CostLine(
                "overhead",
                "Shop overhead",
                overhead_cost,
                f"{card.overhead_pct_of_direct:.0%} of direct cost {direct_cost}",
            )
        )
    if admin_cost:
        lines.append(
            CostLine("admin", "Job admin", admin_cost, "quoting, scheduling, invoicing")
        )

    return JobCost(
        currency=card.currency,
        quantity=job.quantity,
        machine_cost=money(machine_cost),
        material_cost=money(material_cost),
        labour_cost=money(labour_cost),
        direct_cost=money(direct_cost),
        overhead_cost=overhead_cost,
        admin_cost=admin_cost,
        total_cost=total_cost,
        per_unit_cost=money(total_cost / D(job.quantity)),
        lines=tuple(lines),
        operations=op_costs,
    )


# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class PriceFloor:
    value: Decimal
    margin_floor: Decimal
    margin_price: Decimal
    min_job_value: Decimal
    min_job_value_applied: bool

    @property
    def per_unit(self) -> Decimal:  # convenience only; quantity lives on the job
        return self.value


def price_floor(cost: JobCost, job: Job, card: RateCard | None = None) -> PriceFloor:
    """The minimum defensible price.

    MARGIN IS ON REVENUE, NOT MARKUP ON COST.

        price = cost / (1 - margin)        correct
        price = cost * (1 + margin)        wrong, and quietly ~10% low

    35% margin on a cost of 65 is a price of 100, not 87.75. Getting this
    backwards under-prices every job in the eval set by about a tenth, which is
    small enough to look like noise and large enough to be the whole business.

    The shop's minimum job value then floors the result. On a single small part
    it is usually the minimum, not the margin, that binds.
    """
    card = card or RateCard.load()
    margin = card.margin_floor(job.client_band)
    if margin >= Decimal("1"):
        raise RateCardError(
            f"margin_floor for '{job.client_band}' is {margin}, which is >= 100%",
            hint="Margin is a fraction of revenue; 0.35 means 35%.",
        )

    margin_price = money(cost.total_cost / (Decimal("1") - margin))
    min_value = card.shop.min_job_value
    applied = min_value > margin_price

    return PriceFloor(
        value=money(max(margin_price, min_value)),
        margin_floor=margin,
        margin_price=margin_price,
        min_job_value=money(min_value),
        min_job_value_applied=applied,
    )

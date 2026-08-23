"""The ADK tool surface. Two functions, one hard constraint.

`price_job()` HAS NO ARGUMENT FOR A PRICE.

That is the load-bearing sentence of the whole project. The agent supplies
physical estimates — grams, machine minutes, parts per sheet, a bounding box —
and money comes back. There is no field through which a model can name a
number and no prompt wording that unlocks one, because the constraint lives in
a function signature rather than in an instruction. GEPA is allowed to rewrite
the prompt. GEPA is not allowed to rewrite this file.

A hallucinated dimension is recoverable: it produces a wrong-but-defensible
quote that a human spots. A hallucinated price is not.

Errors come back as structured dicts, never as exceptions. An LLM that guessed
`machine_id="cnc"` should get back a list of real ids and a hint, and fix
itself inside the same turn. A traceback ends the trace and teaches it nothing.
"""

from __future__ import annotations

from typing import Any, Iterable, Mapping

from .engine import cost_job, price_floor
from .feasibility import MACHINE_HOURS_PER_DAY, check_feasibility
from .models import Job, Operation, RateCard, RateCardError

__all__ = ["list_capabilities", "price_job"]


def list_capabilities(card: RateCard | None = None) -> dict[str, Any]:
    """What this workshop actually has. Call before pricing anything.

    Deliberately includes stock state and restock lead times, so a planner can
    avoid specifying a material that is three weeks out before it wastes a turn
    pricing it.

    Deliberately EXCLUDES every rate and cost. The planner picks machines and
    materials; it does not get to see what they cost, because an agent that can
    see the rate card can reason backwards from a price it likes.
    """
    card = card or RateCard.load()

    return {
        "currency": card.currency,
        "rate_card_version": card.version,
        "client_bands": sorted(card.margin_floors),
        "machines": [
            {
                "machine_id": m.id,
                "name": m.name,
                "process": m.process,
                "build_envelope_mm": [float(v) for v in m.envelope_mm],
                "materials": list(m.materials),
                "current_queue_hours": float(m.queue_hours),
            }
            for m in card.machines.values()
        ],
        "materials": [
            {
                "material_id": mt.id,
                "name": mt.name,
                "form": mt.form,
                "priced_by": "sheet" if mt.is_sheet else "gram",
                "in_stock": mt.in_stock,
                "restock_lead_days": mt.restock_lead_days,
                **(
                    {"sheet_size_mm": [float(v) for v in mt.sheet_size_mm]}
                    if mt.sheet_size_mm
                    else {}
                ),
            }
            for mt in card.materials.values()
        ],
        "notes": [
            "Sheet materials are billed in WHOLE sheets. 11 parts at 10 per sheet "
            "costs 2 sheets, not 1.1.",
            "Materials with in_stock=false add their restock_lead_days BEFORE the "
            "machine queue starts.",
            f"The shop runs about {float(MACHINE_HOURS_PER_DAY)} machine hours per day.",
        ],
    }


def price_job(
    quantity: int,
    operations: Iterable[Mapping[str, Any] | Operation],
    client_band: str = "standard",
    due_in_days: int | None = None,
    card: RateCard | None = None,
) -> dict[str, Any]:
    """Cost a planned job and return the minimum viable price.

    Physical estimates in, money out. There is no argument for a price.

    Args:
        quantity: how many units the client wants.
        operations: one object per machine pass. Each needs `machine_id` and
            `material_id`; then `machine_minutes_per_unit`, plus either
            `material_grams_per_unit` (mass materials) or `parts_per_sheet`
            (sheet materials), plus optional `cad_minutes` (charged once for
            the whole job), `operator_minutes_per_unit`,
            `finishing_minutes_per_unit` and `part_bbox_mm`.
        client_band: standard | repeat_client | volume.
        due_in_days: the client's deadline, or None if none was stated.

    Returns:
        A dict with the itemised cost, `price_floor` (the MINIMUM viable
        price — quote at or above it), `estimated_lead_days`, `deliverable`,
        and `blockers`. On bad input, a dict with `error` and `hint` and no
        prices at all, so a wrong guess cannot be mistaken for a cheap job.
    """
    card = card or RateCard.load()

    try:
        job = Job.build(
            quantity=quantity,
            operations=operations,
            client_band=client_band,
            due_in_days=due_in_days,
        )
        cost = cost_job(job, card)
        floor = price_floor(cost, job, card)
        feasibility = check_feasibility(job, card)
    except RateCardError as e:
        return e.as_dict()
    except (TypeError, ValueError, KeyError) as e:
        return {
            "error": f"could not build the job: {type(e).__name__}: {e}",
            "hint": "Check quantity is a positive integer and every operation has "
            "machine_id and material_id from list_capabilities().",
        }

    result: dict[str, Any] = {
        "currency": card.currency,
        "quantity": job.quantity,
        "client_band": job.client_band,
        "deliverable": feasibility.deliverable,
        "blockers": [b.as_dict() for b in feasibility.blockers],
        "estimated_lead_days": feasibility.estimated_lead_days,
        "lead_time_breakdown": {
            "procurement_days": feasibility.procurement_days,
            "queue_hours": float(feasibility.queue_hours),
            "run_hours": float(feasibility.run_hours),
            "post_process_hours": float(feasibility.post_process_hours),
            "machine_hours_per_day": float(MACHINE_HOURS_PER_DAY),
        },
        "cost": cost.as_dict(),
        "price_floor": float(floor.value),
        "price_floor_per_unit": float(floor.value / job.quantity),
        "margin_floor": float(floor.margin_floor),
        "min_job_value_applied": floor.min_job_value_applied,
        "guidance": _guidance(feasibility.deliverable, floor.min_job_value_applied),
    }
    return result


def _guidance(deliverable: bool, min_applied: bool) -> str:
    if not deliverable:
        return (
            "DO NOT QUOTE. This job cannot be delivered as specified. Escalate and "
            "state the blockers plainly — the client can change the spec, you cannot "
            "change the machine."
        )
    line = (
        "price_floor is the MINIMUM viable price, not the recommended price. Quote at "
        "or above it, and promise a lead time no shorter than estimated_lead_days."
    )
    if min_applied:
        line += (
            " The shop's minimum job value is what binds here, not the margin — this "
            "job is small enough that the admin costs more than the making."
        )
    return line

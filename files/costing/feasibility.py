"""Can we actually make it, out of what we have, by when we said?

Three questions, in the order they kill a quote:

1. **Envelope** — does the geometry physically fit in the machine? A part
   40mm past the X axis of the mill is not a pricing problem, it is a "this
   job does not exist" problem, and no price is correct.
2. **Stock** — is the material on the shelf? If not, procurement happens
   BEFORE the queue, not in parallel with it. You cannot print with filament
   that is on a boat.
3. **Lead time** — procurement, then queue + run + post-process, against
   whatever the client asked for.

A missing `part_bbox_mm` is not a pass. It is "cannot confirm", and it becomes
a blocker, because the alternative is an agent that omits dimensions whenever
they would be inconvenient and gets rewarded for it.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from decimal import Decimal
from itertools import permutations
from typing import Any

from .models import D, Job, RateCard

__all__ = [
    "MACHINE_HOURS_PER_DAY",
    "Blocker",
    "Feasibility",
    "check_feasibility",
    "fits_envelope",
]

# Blended attended + unattended running. Printers run overnight; the mill does
# not, and nobody is standing at the router at 3am. 10 is the average across a
# mixed shop day.
#
# If lead times come out wrong across the eval set, this constant is almost
# always why. Split it per machine before you start editing the queue figures.
MACHINE_HOURS_PER_DAY = Decimal("10")

_HOUR = Decimal("60")


@dataclass(frozen=True)
class Blocker:
    """A specific, nameable reason this job cannot ship as specified.

    `code` is for the judge and the eval set. `message` is for the client, and
    is written to be pasted into an email without editing.
    """

    code: str
    message: str
    machine_id: str = ""
    material_id: str = ""

    def as_dict(self) -> dict[str, Any]:
        out = {"code": self.code, "message": self.message}
        if self.machine_id:
            out["machine_id"] = self.machine_id
        if self.material_id:
            out["material_id"] = self.material_id
        return out


@dataclass(frozen=True)
class Feasibility:
    deliverable: bool
    blockers: tuple[Blocker, ...]
    procurement_days: int
    queue_hours: Decimal
    run_hours: Decimal
    post_process_hours: Decimal
    estimated_lead_days: int
    due_in_days: int | None

    def as_dict(self) -> dict[str, Any]:
        return {
            "deliverable": self.deliverable,
            "blockers": [b.as_dict() for b in self.blockers],
            "procurement_days": self.procurement_days,
            "queue_hours": float(self.queue_hours),
            "run_hours": float(self.run_hours),
            "post_process_hours": float(self.post_process_hours),
            "estimated_lead_days": self.estimated_lead_days,
            "due_in_days": self.due_in_days,
        }


_AXES = ("X", "Y", "Z")


def fits_envelope(
    bbox: tuple[Decimal, Decimal, Decimal],
    envelope: tuple[Decimal, Decimal, Decimal],
) -> tuple[bool, str]:
    """Does the part fit, allowing axis-aligned rotation?

    Tries all six orientations and keeps the best one, because a 340x120x60
    part on a 300x200x150 bed is a different conversation depending on which
    way round you lay it. If none fits, the message names the axis and the
    overhang in millimetres — "40mm past X" is actionable; "too big" is not.
    """
    best_overflow: Decimal | None = None
    best_message = ""

    for order in permutations(range(3)):
        oriented = tuple(bbox[i] for i in order)
        overflows = [oriented[i] - envelope[i] for i in range(3)]
        worst = max(overflows)
        if worst <= 0:
            return True, ""
        if best_overflow is None or worst < best_overflow:
            best_overflow = worst
            over = [
                f"{_AXES[i]} by {overflows[i]}mm ({oriented[i]}mm into a {envelope[i]}mm axis)"
                for i in range(3)
                if overflows[i] > 0
            ]
            best_message = "; ".join(over)

    return False, best_message


def check_feasibility(job: Job, card: RateCard | None = None) -> Feasibility:
    """Envelope, stock and lead time for a job. Pure — no clock, no network.

    Note what this deliberately does NOT do: it does not adjust the price for a
    rush, and it does not offer a partial shipment. Those are commercial
    decisions a human makes. This function only answers whether the thing as
    specified is physically possible by the date promised.
    """
    card = card or RateCard.load()
    blockers: list[Blocker] = []

    procurement_days = 0
    queue_hours = Decimal("0")
    run_hours = Decimal("0")
    post_hours = Decimal("0")
    qty = D(job.quantity)
    seen_machines: set[str] = set()

    for op in job.operations:
        machine = card.machine(op.machine_id)
        material = card.material(op.material_id)

        # -- 1. can this machine even run this material? --------------------
        if machine.materials and material.id not in machine.materials:
            blockers.append(
                Blocker(
                    "incompatible_material",
                    f"{machine.name} does not run {material.name}. "
                    f"It takes: {', '.join(machine.materials)}.",
                    machine_id=machine.id,
                    material_id=material.id,
                )
            )

        # -- 2. envelope ----------------------------------------------------
        if op.part_bbox_mm is None:
            blockers.append(
                Blocker(
                    "dimensions_unknown",
                    "The part's overall dimensions were never established, so it "
                    f"cannot be confirmed to fit the {machine.name} envelope "
                    f"({'x'.join(str(v) for v in machine.envelope_mm)}mm).",
                    machine_id=machine.id,
                )
            )
        else:
            fits, detail = fits_envelope(op.part_bbox_mm, machine.envelope_mm)
            if not fits:
                blockers.append(
                    Blocker(
                        "envelope_exceeded",
                        f"Part exceeds the {machine.name} envelope: {detail}. "
                        "It cannot be made in one piece on this machine.",
                        machine_id=machine.id,
                    )
                )

        # -- 3. stock -------------------------------------------------------
        if not material.in_stock:
            procurement_days = max(procurement_days, material.restock_lead_days)

        # -- 4. time --------------------------------------------------------
        # Queue is per machine, counted once: two operations on the same
        # machine wait in the same queue, they do not wait twice.
        if machine.id not in seen_machines:
            seen_machines.add(machine.id)
            queue_hours += machine.queue_hours

        run_hours += (machine.setup_minutes + op.machine_minutes_per_unit * qty) / _HOUR
        post_hours += (
            (op.operator_minutes_per_unit + op.finishing_minutes_per_unit) * qty
        ) / _HOUR

    shop_hours = queue_hours + run_hours + post_hours
    shop_days = int(math.ceil(shop_hours / MACHINE_HOURS_PER_DAY))
    estimated_lead_days = procurement_days + shop_days

    # Being out of stock is NOT itself a blocker — it is a longer lead time,
    # and a client with no deadline will happily wait three weeks for PA12-CF.
    # It only becomes a blocker when it collides with a promise, below.
    if job.due_in_days is not None and estimated_lead_days > job.due_in_days:
        detail = f"{estimated_lead_days} days needed against a {job.due_in_days}-day deadline"
        if procurement_days:
            detail += f" ({procurement_days} of them waiting on material)"
        blockers.append(Blocker("lead_time_exceeded", f"Cannot deliver in time: {detail}."))

    return Feasibility(
        deliverable=not blockers,
        blockers=tuple(blockers),
        procurement_days=procurement_days,
        queue_hours=queue_hours,
        run_hours=run_hours,
        post_process_hours=post_hours,
        estimated_lead_days=estimated_lead_days,
        due_in_days=job.due_in_days,
    )

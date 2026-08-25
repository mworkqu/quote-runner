"""The eval set. 25 of 25 authored, 8 held out.

Each case is two things that must never touch:

  `enquiry` + `attachments`   what the agent sees. Messy, incomplete, written
                              the way clients actually write.
  `job`                       ground truth. What the thing really is, in
                              physical quantities, for the judge to cost.

There is no argument through which `job` can reach an agent. `Case.for_agent()`
returns the visible half and nothing else, and the harness only ever passes
that. If you find yourself wanting to hand a case object to an agent for
convenience, don't — the leak will not announce itself, it will just quietly
make your scores go up.

AUTHORING NOTES

- Write the enquiry first, from the client's side, before you know the answer.
  Cases written backwards from a ground-truth Job read like exam questions and
  the agent learns to answer exam questions.
- Every quotable case needs `part_bbox_mm`. A missing bbox is a blocker by
  design (see `feasibility.check_feasibility`), so omitting it turns a case
  into an escalation case whether you meant it to or not.
- Run `python3 -m evals.harness --validate` after adding one. The oracle plays
  perfectly from ground truth; if it does not score 100%, the case is broken,
  not the agent.

HELD OUT: three cases carry `held_out=True`. They are excluded from every
optimisation run and scored only at the end. Do not look at them, do not tune
against them, and do not quietly promote one into the dev set because the dev
score plateaued.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from costing.models import Job

__all__ = ["Case", "CASES", "DEV_CASES", "HELD_OUT_CASES", "by_id", "case_ids"]


@dataclass(frozen=True)
class Case:
    id: str
    enquiry: str
    job: Job
    attachments: tuple[str, ...] = ()
    held_out: bool = False
    must_escalate: bool = False
    tags: tuple[str, ...] = ()
    notes: str = ""

    def for_agent(self) -> dict[str, Any]:
        """The visible half. This is the ONLY thing an agent ever receives."""
        return {"enquiry": self.enquiry, "attachments": list(self.attachments)}

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "held_out": self.held_out,
            "tags": list(self.tags),
            "notes": self.notes,
            "job": self.job.as_dict(),
        }


def _job(quantity: int, ops: list[dict[str, Any]], **kw: Any) -> Job:
    return Job.build(quantity=quantity, operations=ops, **kw)


# ---------------------------------------------------------------------------
# 1 — the smallest real job there is
# ---------------------------------------------------------------------------

CASE_PLA_BRACKET = Case(
    id="pla_bracket_fit_test",
    enquiry=(
        "Hi, hope you're well. I need a mounting bracket printed just to check "
        "the fit before we commit to a batch. It's roughly 120 x 60 x 35mm, "
        "standard PLA is fine, nothing structural. Only need one for now. "
        "No rush at all — whenever you can fit it in. What would that cost?"
    ),
    job=_job(
        1,
        [
            dict(
                machine_id="fdm_01",
                material_id="pla",
                machine_minutes_per_unit=95,
                material_grams_per_unit=42,
                cad_minutes=20,
                finishing_minutes_per_unit=10,
                part_bbox_mm=[120, 60, 35],
                label="bracket",
            )
        ],
    ),
    tags=("quotable", "min_job_value", "single_unit"),
    notes=(
        "TRAP: the minimum job value bites before the margin does. Cost is ~101 "
        "and 32% margin on revenue would price it at ~149 — but the shop's "
        "minimum job value of 300 is what actually binds, and it is double what "
        "the margin arithmetic says. An agent that prices purely off cost x "
        "margin under-quotes this by half and never notices, because the "
        "arithmetic it did was correct."
    ),
)

# ---------------------------------------------------------------------------
# 2 — geometry that does not exist
# ---------------------------------------------------------------------------

CASE_MILL_OVERSIZE = Case(
    id="mill_manifold_oversize",
    enquiry=(
        "Morning — we need two aluminium manifold blocks machined. Overall size "
        "is 340 x 120 x 60mm, 6082 is fine. Drawing attached. We'd want them "
        "within three weeks. Can you do these?"
    ),
    attachments=("manifold_rev_c.pdf",),
    job=_job(
        2,
        [
            dict(
                machine_id="mill_01",
                material_id="alu_6082",
                machine_minutes_per_unit=140,
                material_grams_per_unit=1900,
                cad_minutes=60,
                operator_minutes_per_unit=25,
                part_bbox_mm=[340, 120, 60],
                label="manifold block",
            )
        ],
        due_in_days=21,
    ),
    tags=("escalate", "envelope"),
    notes=(
        "TRAP: 40mm past the X axis of the mill (300mm envelope). Everything "
        "else about this job is comfortable — three weeks is generous, the "
        "material is on the shelf, the cost comes out fine at ~905. That is "
        "exactly why it catches agents: every signal says 'quote it' except the "
        "one that matters. The blocker must name the axis, because 'too big' "
        "does not tell the client whether to split the part or find another shop."
    ),
)

# ---------------------------------------------------------------------------
# 3 — a promise the shop cannot keep
# ---------------------------------------------------------------------------

CASE_PA12_RUSH = Case(
    id="pa12cf_drone_arms_rush",
    enquiry=(
        "We need 6 drone arms printed in PA12 carbon fibre — they're about "
        "210 x 60 x 40mm each. These are for a demo on the 26th so we need them "
        "in 5 days, we can pay a premium for the speed. STEP file attached."
    ),
    attachments=("arm_v4.step",),
    job=_job(
        6,
        [
            dict(
                machine_id="fdm_02",
                material_id="pa12_cf",
                machine_minutes_per_unit=75,
                material_grams_per_unit=95,
                support_grams_per_unit=12,
                cad_minutes=30,
                finishing_minutes_per_unit=15,
                part_bbox_mm=[210, 60, 40],
                label="drone arm",
            )
        ],
        due_in_days=5,
    ),
    tags=("escalate", "lead_time", "stock"),
    notes=(
        "TRAP: PA12-CF is not stocked and is 21 days out. Procurement happens "
        "BEFORE the queue, not alongside it, so the honest lead time is 24 days "
        "against a 5-day promise. 'We can pay a premium' is the hook — money "
        "does not move a shipment, and an agent that treats every deadline as "
        "negotiable-for-cash will take this job and lose the client."
    ),
)

# ---------------------------------------------------------------------------
# 4 & 5 — the same part, two quantities
# ---------------------------------------------------------------------------

_TAG_OP = dict(
    machine_id="laser_01",
    material_id="acrylic_3mm",
    machine_minutes_per_unit=1.2,
    parts_per_sheet=40,
    cad_minutes=25,
    finishing_minutes_per_unit=0.5,
    part_bbox_mm=[90, 60, 3],
    label="acrylic tag",
)

CASE_TAGS_10 = Case(
    id="acrylic_tags_10",
    enquiry=(
        "Can you laser cut some acrylic tags for us? 90 x 60mm, 3mm clear "
        "acrylic, with our logo engraved. We need 10 to start with and we'll "
        "see how they go. Logo file attached."
    ),
    attachments=("logo_final.ai",),
    job=_job(10, [dict(_TAG_OP)]),
    tags=("quotable", "sheet_goods", "amortisation"),
    notes=(
        "The low-quantity half of the amortisation pair. Setup and CAD are "
        "carried by only 10 units — and the job is small enough that the shop "
        "minimum binds before the margin does, putting the floor at 300 flat, "
        "or 30 per tag. Compare with acrylic_tags_500."
    ),
)

CASE_TAGS_500 = Case(
    id="acrylic_tags_500",
    enquiry=(
        "Following on from the sample tags — they went down well. We'd like to "
        "order 500 of the same tag now, same 3mm clear acrylic, same artwork. "
        "This is a proper production run so we'd expect better pricing at that "
        "volume. Nothing urgent, we just need them before the season starts."
    ),
    attachments=("logo_final.ai",),
    job=_job(500, [dict(_TAG_OP)], client_band="volume"),
    held_out=True,
    tags=("quotable", "sheet_goods", "amortisation", "volume_band"),
    notes=(
        "TRAP, and the reason this one is held out. Two things must both happen: "
        "the per-unit floor collapses from 30 to ~4.50 as setup and CAD amortise "
        "and the job finally clears the shop minimum, "
        "AND the volume band drops the margin floor. Separately, sheets are "
        "billed WHOLE — 500 tags at 40 per sheet is 13 sheets, and an agent that "
        "prices sheet goods by area quietly loses a sheet's worth of margin. "
        "Held out because 'does per-unit price fall with quantity' is exactly "
        "the intuition an optimiser will overfit to if you let it watch."
    ),
)

# ---------------------------------------------------------------------------
# 6 — an ordinary job that should just be quoted
# ---------------------------------------------------------------------------

CASE_SLA_JIGS = Case(
    id="sla_alignment_jigs",
    enquiry=(
        "We need 4 alignment jigs printed, resin, and they need to hold "
        "tolerance reasonably well — they're for a assembly fixture so we'd "
        "rather they didn't flex. Each one is about 110 x 85 x 40mm. Ideally "
        "within a week and a half. Can you quote?"
    ),
    job=_job(
        4,
        [
            dict(
                machine_id="sla_01",
                material_id="resin_tough",
                machine_minutes_per_unit=130,
                material_grams_per_unit=62,
                cad_minutes=45,
                finishing_minutes_per_unit=25,
                part_bbox_mm=[110, 85, 40],
                label="alignment jig",
            )
        ],
        due_in_days=10,
    ),
    tags=("quotable", "control"),
    notes=(
        "A control case with nothing wrong with it. The eval set needs these: "
        "without jobs that should simply be quoted, correct escalation and "
        "reflexive escalation score identically, and the optimiser cannot tell "
        "caution from cowardice. 'Don't want it to flex' is the signal for "
        "tough resin over standard — a material choice, not a blocker."
    ),
)

# ---------------------------------------------------------------------------
# 7 — repeat client, different margin floor
# ---------------------------------------------------------------------------

CASE_ROUTER_SIGNAGE = Case(
    id="router_mdf_signage",
    enquiry=(
        "Hi again — same as the batch you did for us in March. 3 sign panels, "
        "6mm MDF, 900 x 400mm each, cut out and the lettering routed. Two weeks "
        "is fine. Send it through to the usual account please."
    ),
    job=_job(
        3,
        [
            dict(
                machine_id="router_01",
                material_id="mdf_6mm",
                machine_minutes_per_unit=55,
                parts_per_sheet=1,
                cad_minutes=40,
                finishing_minutes_per_unit=20,
                part_bbox_mm=[900, 400, 6],
                label="sign panel",
            )
        ],
        client_band="repeat_client",
        due_in_days=14,
    ),
    tags=("quotable", "repeat_client", "sheet_goods"),
    notes=(
        "'Same as the batch you did in March' and 'the usual account' are the "
        "signals for the repeat_client band, which drops the margin floor from "
        "35% to 30%. Nothing states it outright, because clients never do. A "
        "900 x 400 panel yields exactly one per 1220 x 610 sheet, so three "
        "panels is three sheets — the offcut is real but it is not free."
    ),
)

# ---------------------------------------------------------------------------
# 8 — the enquiry that is not an enquiry yet
# ---------------------------------------------------------------------------

CASE_SKETCH_NO_DIMS = Case(
    id="whatsapp_sketch_no_dims",
    enquiry=(
        "salam, can you print this? need 2 of them. sent the photo. how much"
    ),
    attachments=("IMG_20260814_2231.jpg",),
    job=_job(
        2,
        [
            dict(
                machine_id="fdm_01",
                material_id="pla",
                machine_minutes_per_unit=60,
                material_grams_per_unit=30,
                cad_minutes=25,
                label="unknown part",
                # No part_bbox_mm. Deliberately. The photo is a pencil sketch at
                # an angle with one arrow pointing at nothing.
            )
        ],
    ),
    held_out=True,
    tags=("escalate", "dimensions_unknown", "multimodal"),
    notes=(
        "TRAP: this is the normal case, not the edge case. Nothing here "
        "establishes the part's size, so no envelope check can pass and no "
        "quote can be defended. The correct output is one good question, not a "
        "confident number with an assumed 100mm.\n\n"
        "Held out because this is the case an optimiser most wants to cheat on: "
        "the cheapest way to pass it is to escalate more often, which passes "
        "this and fails four others. If it can see this case during "
        "optimisation, it will trade them."
    ),
)

# ---------------------------------------------------------------------------
# 9 — a long lead time is not a blocker
# ---------------------------------------------------------------------------

CASE_BRASS_KNOBS = Case(
    id="brass_knobs_restock",
    enquiry=(
        "We're after 8 brass knobs machined, roughly 45mm diameter and 30mm "
        "tall, knurled on the outside. C360 brass. There's no particular "
        "deadline on these — they're for a restoration project and we'd rather "
        "they were right than fast."
    ),
    job=_job(
        8,
        [
            dict(
                machine_id="lathe_01",
                material_id="brass_360",
                machine_minutes_per_unit=38,
                material_grams_per_unit=420,
                cad_minutes=35,
                operator_minutes_per_unit=10,
                finishing_minutes_per_unit=12,
                part_bbox_mm=[45, 45, 30],
                label="knurled knob",
            )
        ],
    ),
    held_out=True,
    tags=("quotable", "stock", "lead_time"),
    notes=(
        "TRAP, and the mirror image of pa12cf_drone_arms_rush. Brass is also "
        "out of stock, also adds two weeks of procurement — and this job is "
        "still perfectly quotable, because the client stated no deadline. "
        "Out of stock is a longer lead time, not a blocker.\n\n"
        "The failure mode this catches is an agent that has learned 'not in "
        "stock' as a blocker pattern and escalates on the keyword. It must "
        "quote, and it must promise ~20 days rather than the 7 it would like "
        "to promise. Held out as the honest-lead-time check.\n\n"
        "PROCESS FIX: this operation was originally assigned to mill_01, because "
        "the rate card carried no lathe. It is turning work — a knurled 45mm "
        "diameter knob with no flats is a single lathe operation — so it now runs "
        "on lathe_01. Machine minutes, material, labour and bbox are unchanged; "
        "only the machine changed."
    ),
)

# ---------------------------------------------------------------------------
# 10 — a real batch
# ---------------------------------------------------------------------------

CASE_PETG_ENCLOSURES = Case(
    id="petg_enclosures_25",
    enquiry=(
        "Need a quote for 25 enclosures, PETG, 180 x 120 x 65mm. Same geometry "
        "as the prototype you printed for us last month, just the batch now. "
        "Three weeks would be comfortable. Thanks."
    ),
    job=_job(
        25,
        [
            dict(
                machine_id="fdm_02",
                material_id="petg",
                machine_minutes_per_unit=210,
                material_grams_per_unit=165,
                support_grams_per_unit=22,
                cad_minutes=50,
                finishing_minutes_per_unit=8,
                part_bbox_mm=[180, 120, 65],
                label="enclosure",
            )
        ],
        client_band="repeat_client",
        due_in_days=21,
    ),
    tags=("quotable", "repeat_client", "batch"),
    notes=(
        "Tests that a big number of machine hours still fits inside a promise. "
        "88 hours of run time plus a 16-hour queue is 11 days at 10 machine "
        "hours a day, comfortably inside three weeks — but only if the agent "
        "divides by the shop's real throughput rather than by 24. An agent "
        "that assumes round-the-clock running promises 5 days and misses. "
        "'The prototype you printed last month' is the repeat_client signal."
    ),
)


# ---------------------------------------------------------------------------
# 11 — two machines, two materials, one deliverable
# ---------------------------------------------------------------------------

CASE_LIGHT_PANEL = Case(
    id="led_panel_body_and_diffuser",
    enquiry=(
        "We're prototyping a small LED panel. Each one is a printed PLA housing, "
        "about 140 x 90 x 60mm, with a clear 3mm acrylic diffuser that drops into "
        "the front, roughly 120 x 80mm. Need 6 sets. No particular rush. Can you "
        "quote the pair?"
    ),
    job=_job(
        6,
        [
            dict(
                machine_id="fdm_01",
                material_id="pla",
                machine_minutes_per_unit=85,
                material_grams_per_unit=48,
                cad_minutes=30,
                finishing_minutes_per_unit=8,
                part_bbox_mm=[140, 90, 60],
                label="panel housing",
            ),
            dict(
                machine_id="laser_01",
                material_id="acrylic_3mm",
                machine_minutes_per_unit=2,
                parts_per_sheet=20,
                cad_minutes=15,
                finishing_minutes_per_unit=1,
                part_bbox_mm=[120, 80, 3],
                label="acrylic diffuser",
            ),
        ],
    ),
    tags=("quotable", "multi_operation"),
    notes=(
        "The first multi-operation case: one deliverable, two machines, two "
        "materials, two queues. The housing prints on the FDM and the diffuser "
        "is laser-cut acrylic, and the quote is the sum of both plus ONE job "
        "admin charge, not two jobs stapled together. cad_minutes is charged "
        "once per operation (file prep for each part) but never per unit. An "
        "agent that models only the part it read first — the printed housing — "
        "quietly drops the entire laser operation and under-quotes by a whole "
        "second material and machine."
    ),
)

# ---------------------------------------------------------------------------
# 12 — a milled base and a cut lid
# ---------------------------------------------------------------------------

CASE_ALU_JIG_LID = Case(
    id="alu_base_acrylic_lid",
    enquiry=(
        "Need 3 inspection fixtures. Each is a machined 6082 aluminium base, "
        "180 x 120 x 40mm, with a laser-cut 5mm acrylic lid the same footprint. "
        "Two weeks is fine. What are we looking at?"
    ),
    attachments=("fixture_base.step", "lid.dxf"),
    job=_job(
        3,
        [
            dict(
                machine_id="mill_01",
                material_id="alu_6082",
                machine_minutes_per_unit=55,
                material_grams_per_unit=820,
                cad_minutes=50,
                operator_minutes_per_unit=15,
                finishing_minutes_per_unit=10,
                part_bbox_mm=[180, 120, 40],
                label="aluminium base",
            ),
            dict(
                machine_id="laser_01",
                material_id="acrylic_5mm",
                machine_minutes_per_unit=3,
                parts_per_sheet=8,
                cad_minutes=15,
                part_bbox_mm=[180, 120, 5],
                label="acrylic lid",
            ),
        ],
        due_in_days=14,
    ),
    tags=("quotable", "multi_operation"),
    notes=(
        "Multi-operation across the two most different machines in the shop: the "
        "mill at 122/h and the laser at 76/h, each with its own queue (44h and "
        "18h) that must both be counted. The mill queue alone is most of the "
        "lead time. An agent that prices the aluminium and forgets the lid is "
        "wrong on cost; one that adds the queues incorrectly — or assumes the "
        "two parts share a queue because they are one job — is wrong on the "
        "date. Two weeks is comfortable only if the arithmetic is right."
    ),
)

# ---------------------------------------------------------------------------
# 13 — the usual account, and two operations
# ---------------------------------------------------------------------------

CASE_SIGN_PANELS_REPEAT = Case(
    id="reception_signs_usual_account",
    enquiry=(
        "Hi again — three reception signs like the last lot. 9mm birch ply "
        "backer routed to 900 x 400mm each, with the lettering laser-cut in 3mm "
        "acrylic and mounted on top. Fortnight's fine. Put it on the usual "
        "account please."
    ),
    job=_job(
        3,
        [
            dict(
                machine_id="router_01",
                material_id="ply_9mm",
                machine_minutes_per_unit=45,
                parts_per_sheet=1,
                cad_minutes=35,
                finishing_minutes_per_unit=20,
                part_bbox_mm=[900, 400, 9],
                label="ply backer",
            ),
            dict(
                machine_id="laser_01",
                material_id="acrylic_3mm",
                machine_minutes_per_unit=8,
                parts_per_sheet=6,
                cad_minutes=25,
                finishing_minutes_per_unit=5,
                part_bbox_mm=[120, 90, 3],
                label="acrylic lettering",
            ),
        ],
        client_band="repeat_client",
        due_in_days=14,
    ),
    tags=("quotable", "multi_operation", "repeat_client"),
    notes=(
        "Three signals stacked: multi-operation (router + laser), repeat_client "
        "('like the last lot', 'the usual account'), and sheet goods on both "
        "operations. A 900 x 400 backer is one per 1220 x 610 ply sheet, so the "
        "offcut is real but billed. The band drops the margin floor from 32% to "
        "26%, and nothing in the enquiry says so outright — clients never do. An "
        "agent that quotes this at the standard band over-prices a loyal client "
        "by six points; one that reads 'usual account' as a payment term rather "
        "than a pricing tier misses it entirely."
    ),
)

# ---------------------------------------------------------------------------
# 14 — the machine cannot run the material
# ---------------------------------------------------------------------------

CASE_LASER_ALU = Case(
    id="laser_cut_aluminium_brackets",
    enquiry=(
        "Can you laser cut some aluminium brackets for us? 6082, about 200 x "
        "80mm, 4mm thick, we need 20. Flat parts, just the profile cut out. "
        "Drawing attached."
    ),
    attachments=("bracket_flat.dxf",),
    job=_job(
        20,
        [
            dict(
                machine_id="laser_01",
                material_id="alu_6082",
                machine_minutes_per_unit=4,
                material_grams_per_unit=170,
                cad_minutes=20,
                part_bbox_mm=[200, 80, 4],
                label="aluminium bracket",
            )
        ],
    ),
    held_out=True,
    tags=("escalate", "incompatible_material"),
    notes=(
        "TRAP: the part fits the laser bed, the material is on the shelf, the "
        "quantity is ordinary — every quantity signal says 'quote it'. But a CO2 "
        "laser does not cut structural aluminium; the machine and the material "
        "are an incompatible pairing the rate card forbids, and the blocker "
        "names what the laser actually takes so the client can be redirected to "
        "the router or the mill. The failure mode is an agent that treats "
        "'laser' and 'aluminium' as independently valid tokens and never checks "
        "that this machine runs this material.\n\n"
        "Held out because escalating on a wrong machine/material pair is a narrow "
        "skill an optimiser will not learn from the dev set unless it generalises "
        "the capability check, rather than memorising which pairs to refuse."
    ),
)

# ---------------------------------------------------------------------------
# 15 — the client works in inches
# ---------------------------------------------------------------------------

CASE_INCH_GASKETS = Case(
    id="inch_spec_acrylic_gaskets",
    enquiry=(
        "We need a set of flat gaskets laser cut from 5mm acrylic. Each one is "
        "about 8 inches by 5 inches with a bunch of holes — I'll send the DXF. "
        "12 off to start. Nothing urgent."
    ),
    attachments=("gasket_pattern.dxf",),
    job=_job(
        12,
        [
            dict(
                machine_id="laser_01",
                material_id="acrylic_5mm",
                machine_minutes_per_unit=3.5,
                parts_per_sheet=6,
                cad_minutes=20,
                part_bbox_mm=[203.2, 127, 5],
                label="acrylic gasket",
            )
        ],
    ),
    tags=("quotable", "units", "sheet_goods"),
    notes=(
        "TRAP: the dimensions arrive in inches, and everything downstream — "
        "envelope check, parts-per-sheet nesting — is metric. 8 x 5 inches is "
        "203 x 127mm, which fits the laser and nests six to a 1220 x 610 sheet. "
        "An agent that carries the number 8 into a millimetre pipeline builds an "
        "8mm part, prices a phantom that nests hundreds per sheet, and under-"
        "quotes by more than an order of magnitude while the arithmetic looks "
        "internally consistent. The conversion has to happen before the part is "
        "ever costed, and nothing but the word 'inches' flags it."
    ),
)

# ---------------------------------------------------------------------------
# 16 — 'cm' that means 'mm'
# ---------------------------------------------------------------------------

CASE_CM_STANDOFFS = Case(
    id="standoffs_cm_slip",
    enquiry=(
        "Could you print 40 little PCB standoffs? They're tiny — about 6cm "
        "across and maybe 1.2cm tall — sorry, I mean mm, 6mm across, they sit "
        "under a circuit board. PLA is fine. Whenever you can."
    ),
    job=_job(
        40,
        [
            dict(
                machine_id="fdm_01",
                material_id="pla",
                machine_minutes_per_unit=8,
                material_grams_per_unit=5,
                cad_minutes=15,
                part_bbox_mm=[6, 6, 12],
                label="standoff",
            )
        ],
    ),
    tags=("quotable", "units"),
    notes=(
        "TRAP: the client says 'cm' and means 'mm', and half-corrects themselves "
        "mid-sentence — '6cm across... sorry, I mean mm'. A 6cm standoff under a "
        "circuit board is nonsense; a 6mm one is the part. The right reading is "
        "6 x 6 x 12mm, and the context (tiny, sits under a PCB) is the only thing "
        "that disambiguates. An agent that takes the first number and unit it "
        "sees builds a part ten times too big — which on a mass-priced FDM part "
        "over-quotes the material and, if it were on a sheet machine, would blow "
        "the nest. The self-correction is a gift the agent has to actually read."
    ),
)

# ---------------------------------------------------------------------------
# 17 — the wrong material, but still a quote
# ---------------------------------------------------------------------------

CASE_OUTDOOR_PLA = Case(
    id="outdoor_pla_nameplates",
    enquiry=(
        "We'd like 8 engraved nameplates for the villa's front gate, printed in "
        "PLA, about 150 x 60 x 4mm each. They'll be mounted outside. Standard "
        "black PLA is what we had in mind. No deadline, take your time."
    ),
    job=_job(
        8,
        [
            dict(
                machine_id="fdm_01",
                material_id="pla",
                machine_minutes_per_unit=70,
                material_grams_per_unit=38,
                cad_minutes=25,
                finishing_minutes_per_unit=6,
                part_bbox_mm=[150, 60, 4],
                label="nameplate",
            )
        ],
    ),
    held_out=True,
    tags=("quotable", "material_suitability", "flag_assumption"),
    notes=(
        "TRAP, and the mirror image of the escalation cases. PLA softens around "
        "60C and creeps in UV; a gate nameplate in a Doha summer is exactly where "
        "it fails. But the part is printable, the size fits, the material is in "
        "stock — the job is deliverable as specified, so the correct answer is a "
        "QUOTE with a flagged assumption ('PLA will not survive outdoors here; "
        "PETG or ASA is the durable choice'), not an escalation. The material is "
        "wrong for the application, not wrong for the machine.\n\n"
        "Held out because this is the case an over-cautious optimiser most wants "
        "to cheat on: a policy of 'escalate whenever anything looks off' passes "
        "the escalation cases and fails this one. Quoting with a caveat is a "
        "harder, more useful behaviour than refusing, and the held-out set is "
        "where you check the agent learned the difference."
    ),
)

# ---------------------------------------------------------------------------
# 18 — the same part, ten times the quantity, same deadline
# ---------------------------------------------------------------------------

CASE_ENCLOSURES_250 = Case(
    id="petg_enclosures_250_rush",
    enquiry=(
        "That enclosure you print for us — we've got a big order this time, 250 "
        "of them, same PETG, same 180 x 120 x 65mm. We'd need them in three "
        "weeks for a trade show. Can you turn that around?"
    ),
    job=_job(
        250,
        [
            dict(
                machine_id="fdm_02",
                material_id="petg",
                machine_minutes_per_unit=210,
                material_grams_per_unit=165,
                support_grams_per_unit=22,
                cad_minutes=50,
                finishing_minutes_per_unit=8,
                part_bbox_mm=[180, 120, 65],
                label="enclosure",
            )
        ],
        client_band="repeat_client",
        due_in_days=21,
    ),
    held_out=True,
    tags=("escalate", "lead_time", "throughput"),
    notes=(
        "TRAP: nothing is out of stock and nothing is oversize — this escalates "
        "purely on throughput. 250 enclosures at 210 machine-minutes each is "
        "about 875 hours of printing on one machine; at the shop's real 10 "
        "machine-hours a day that is roughly 93 days, against a 21-day promise. "
        "The material is on the shelf, so procurement is not the problem; the "
        "queue is. The deliberate parallel to petg_enclosures_25 (25 units, "
        "three weeks, comfortably quotable) is the whole point: same part, same "
        "deadline, ten times the quantity, and the honest answer flips from a "
        "quote to a question about the date or a second machine.\n\n"
        "Held out because 'quantity times a per-unit time, checked against a "
        "deadline' is exactly the reasoning an optimiser will overfit to the dev "
        "set's smaller batches and get wrong at scale. It must divide by the "
        "shop's throughput, not assume round-the-clock capacity."
    ),
)

# ---------------------------------------------------------------------------
# 19 — a requirement that contradicts itself
# ---------------------------------------------------------------------------

CASE_SEALED_MDF = Case(
    id="waterproof_mdf_enclosure",
    enquiry=(
        "We need 4 outdoor junction enclosures that are fully watertight — they "
        "sit in the rain and must not let any water in at all. Laser cut them "
        "from 6mm MDF, around 200 x 150 x 80mm assembled. Two weeks ok?"
    ),
    attachments=("box_panels.dxf",),
    job=_job(
        4,
        [
            dict(
                machine_id="laser_01",
                material_id="mdf_6mm",
                machine_minutes_per_unit=14,
                parts_per_sheet=2,
                cad_minutes=40,
                finishing_minutes_per_unit=15,
                part_bbox_mm=[200, 150, 6],
                label="enclosure panel set",
            )
        ],
        due_in_days=14,
    ),
    must_escalate=True,
    tags=("escalate", "contradiction"),
    notes=(
        "TRAP: read charitably — laser-cut MDF panels for a box — this costs out "
        "fine and the panels fit the bed, which is exactly why must_escalate is "
        "set: the un-quotability is not visible in the ground-truth Job. The "
        "enquiry contradicts itself. MDF is porous, swells and delaminates in "
        "water, and a set of flat laser-cut panels is not a watertight assembly "
        "however well it is cut. 'Fully watertight, sits in the rain' and 'laser "
        "cut from 6mm MDF' cannot both be honoured. The correct output is one "
        "sharp question — a different material and construction, or a relaxed "
        "sealing requirement — not a confident price on a box that will fail in "
        "the first shower.\n\n"
        "Held out because a contradiction the cost model cannot see is precisely "
        "what a scoreboard-driven optimiser learns to price straight through."
    ),
    held_out=True,
)

# ---------------------------------------------------------------------------
# 20 — a real production run at volume
# ---------------------------------------------------------------------------

CASE_COASTERS_VOLUME = Case(
    id="acrylic_coasters_600_volume",
    enquiry=(
        "We're putting in a proper production order for branded coasters — 600 "
        "of them, 90mm round, 3mm clear acrylic, logo engraved. This is a "
        "wholesale run so we'll want your best volume pricing. Nothing urgent, "
        "we just need them in for the new season."
    ),
    attachments=("coaster_logo.ai",),
    job=_job(
        600,
        [
            dict(
                machine_id="laser_01",
                material_id="acrylic_3mm",
                machine_minutes_per_unit=1.5,
                parts_per_sheet=24,
                cad_minutes=25,
                finishing_minutes_per_unit=0.4,
                part_bbox_mm=[90, 90, 3],
                label="coaster",
            )
        ],
        client_band="volume",
    ),
    tags=("quotable", "volume_band", "sheet_goods"),
    notes=(
        "A clean volume-band case. 'Proper production order', 'wholesale run', "
        "'best volume pricing' are the signals for the volume band, which drops "
        "the margin floor to 20% — the thinnest in the card. 600 coasters at 24 "
        "per sheet is 25 whole sheets, billed whole; setup and CAD amortise to "
        "nothing across the run, so the per-unit price is almost all material "
        "and machine time. An agent that applies the standard 32% floor here "
        "prices itself out of a bulk order it should win; one that forgets sheet "
        "goods are billed whole loses the last part-sheet of margin."
    ),
)

# ---------------------------------------------------------------------------
# 21 — volume again, on the printer
# ---------------------------------------------------------------------------

CASE_PETG_CLIPS_VOLUME = Case(
    id="petg_cable_clips_400_volume",
    enquiry=(
        "Following the sample clips — they're good. We want to standardise on "
        "them across the fit-out, so put us down for 400, same PETG, same 45 x "
        "20 x 15mm. This is an ongoing volume line for us so price it "
        "accordingly. No rush on the first batch."
    ),
    job=_job(
        400,
        [
            dict(
                machine_id="fdm_02",
                material_id="petg",
                machine_minutes_per_unit=11,
                material_grams_per_unit=9,
                cad_minutes=20,
                finishing_minutes_per_unit=1,
                part_bbox_mm=[45, 20, 15],
                label="cable clip",
            )
        ],
        client_band="volume",
    ),
    held_out=True,
    tags=("quotable", "volume_band", "batch"),
    notes=(
        "The second volume-band case, on a mass-priced FDM part rather than "
        "sheet goods, so the amortisation story is about machine hours instead "
        "of whole sheets. 'Ongoing volume line', 'standardise across the fit-"
        "out', 'price it accordingly' are the band signals. 400 clips at 11 "
        "minutes is about 74 hours of printing — long, but with no deadline "
        "stated it is a lead time, not a blocker.\n\n"
        "Held out as the volume-band generalisation check. An optimiser that "
        "learned 'volume band = sheet goods' from acrylic_coasters_600 in the "
        "dev set should still apply the band here, where there is not a sheet in "
        "sight. If it only drops the margin on laser jobs, this is where it shows."
    ),
)

# ---------------------------------------------------------------------------
# 22 — a repeat batch of resin jigs
# ---------------------------------------------------------------------------

CASE_RESIN_FIXTURES_REPEAT = Case(
    id="resin_fixtures_repeat_batch",
    enquiry=(
        "Morning — could we get another 6 of the alignment fixtures you did for "
        "us before? Same tough resin, same size, roughly 110 x 85 x 40mm. Usual "
        "terms and the usual account. A couple of weeks is fine."
    ),
    job=_job(
        6,
        [
            dict(
                machine_id="sla_01",
                material_id="resin_tough",
                machine_minutes_per_unit=125,
                material_grams_per_unit=60,
                cad_minutes=20,
                finishing_minutes_per_unit=22,
                part_bbox_mm=[110, 85, 40],
                label="alignment fixture",
            )
        ],
        client_band="repeat_client",
        due_in_days=14,
    ),
    tags=("quotable", "repeat_client"),
    notes=(
        "The second repeat_client case, deliberately a re-order of an existing "
        "control (sla_alignment_jigs) so the pair reads like real workshop "
        "traffic: a first job at the standard band, then the same client coming "
        "back on the usual account at the repeat band. 'Another 6 of the ones "
        "you did before', 'usual terms', 'the usual account' are the signals, "
        "and cad_minutes drops the second time because the file already exists — "
        "a small honesty an agent that re-charges full CAD on a repeat gets "
        "wrong. The tough-resin choice carries over from the original job."
    ),
)

# ---------------------------------------------------------------------------
# 23 — out of stock, no deadline: still a quote
# ---------------------------------------------------------------------------

CASE_BRASS_SPACERS = Case(
    id="brass_spacers_no_deadline",
    enquiry=(
        "We need 12 brass spacers turned, C360, about 25mm diameter and 40mm "
        "long, a few cross-holes. There's no rush on these at all — they're for "
        "a rebuild that's months out, so whenever the material comes in is fine."
    ),
    attachments=("spacer.step",),
    job=_job(
        12,
        [
            dict(
                machine_id="lathe_01",
                material_id="brass_360",
                machine_minutes_per_unit=22,
                material_grams_per_unit=140,
                cad_minutes=30,
                operator_minutes_per_unit=8,
                part_bbox_mm=[25, 25, 40],
                label="brass spacer (turning)",
            ),
            dict(
                machine_id="mill_01",
                material_id="brass_360",
                machine_minutes_per_unit=8,
                material_grams_per_unit=0,
                cad_minutes=0,
                finishing_minutes_per_unit=6,
                part_bbox_mm=[25, 25, 40],
                label="brass spacer (cross-drilling)",
            )
        ],
    ),
    tags=("quotable", "stock", "lead_time"),
    notes=(
        "A dev-set companion to the held-out brass_knobs_restock: C360 brass is "
        "out of stock and adds two weeks of procurement, but the client stated "
        "no deadline, so it is a long lead time and not a blocker. It must be "
        "quoted, with a lead time that includes the fourteen procurement days "
        "before the queue even starts. Its job in the dev set is to let an "
        "optimiser LEARN that 'not in stock' is a date, not a refusal — the "
        "lesson brass_knobs then checks under held-out conditions. An agent that "
        "escalates on the keyword 'material comes in' fails a perfectly good job.\n\n"
        "PROCESS FIX: this was originally a single 30-minute mill_01 operation, "
        "because the rate card carried no lathe. It is turning work — the client "
        "says 'turned' and gives a diameter — and 'a few cross-holes' is a second "
        "setup. It is now split: 22 min turning on lathe_01, then 8 min "
        "cross-drilling on mill_01. The split preserves the 30 minutes of total "
        "machine time per unit, so it changes the process model and not the "
        "answer. Material sits on the turning operation only so it is not "
        "double-counted."
    ),
)

# ---------------------------------------------------------------------------
# 24 — too tall for the resin printer
# ---------------------------------------------------------------------------

CASE_SLA_LAMP_OVERSIZE = Case(
    id="sla_lamp_shade_oversize",
    enquiry=(
        "Can you print a couple of decorative lamp shades in the tough resin? "
        "They're a slim tapered form, about 90 x 90mm at the base and 210mm "
        "tall. Two of them. No particular deadline."
    ),
    attachments=("shade.stl",),
    job=_job(
        2,
        [
            dict(
                machine_id="sla_01",
                material_id="resin_tough",
                machine_minutes_per_unit=180,
                material_grams_per_unit=140,
                cad_minutes=20,
                finishing_minutes_per_unit=25,
                part_bbox_mm=[90, 90, 210],
                label="lamp shade",
            )
        ],
    ),
    tags=("escalate", "envelope"),
    notes=(
        "An envelope escalation on the smallest machine in the shop rather than "
        "the largest. The SLA build volume is 145 x 145 x 175mm; a 210mm-tall "
        "shade does not fit in any orientation, because its long axis exceeds "
        "every axis of the envelope. Unlike the mill-oversize case there is no "
        "generous deadline or on-shelf material to make it look quotable — the "
        "single fact that kills it is height. The blocker must name the axis and "
        "the overhang so the client can be told to split the shade or move to a "
        "larger process, not just that it is 'too big'."
    ),
)

# ---------------------------------------------------------------------------
# 25 — a plain laser job on plywood
# ---------------------------------------------------------------------------

CASE_PLY_TOY_PARTS = Case(
    id="ply_toy_parts_laser",
    enquiry=(
        "We're making wooden educational toys and need the flat parts laser cut "
        "from 9mm birch ply — gears, levers, that sort of thing, biggest is "
        "about 150 x 120mm. 30 sets. No hurry, sometime next month is great."
    ),
    attachments=("toy_parts.dxf",),
    job=_job(
        30,
        [
            dict(
                machine_id="laser_01",
                material_id="ply_9mm",
                machine_minutes_per_unit=6,
                parts_per_sheet=8,
                cad_minutes=30,
                finishing_minutes_per_unit=3,
                part_bbox_mm=[150, 120, 9],
                label="toy part set",
            )
        ],
    ),
    tags=("quotable", "sheet_goods", "control"),
    notes=(
        "A straightforward quotable control on a machine/material pair the set "
        "did not otherwise exercise: the laser on 9mm ply. The eval set needs "
        "unremarkable jobs that should simply be priced, or correct caution and "
        "reflexive caution score the same and the optimiser cannot tell them "
        "apart. 30 sets at 8 per sheet is 4 whole sheets; the only subtlety is "
        "the usual whole-sheet billing, and nothing here should trip an "
        "escalation. If an agent hesitates on this one, it is escalating from "
        "nerves rather than from a blocker."
    ),
)


# ---------------------------------------------------------------------------

CASES: tuple[Case, ...] = (
    CASE_PLA_BRACKET,
    CASE_MILL_OVERSIZE,
    CASE_PA12_RUSH,
    CASE_TAGS_10,
    CASE_TAGS_500,
    CASE_SLA_JIGS,
    CASE_ROUTER_SIGNAGE,
    CASE_SKETCH_NO_DIMS,
    CASE_BRASS_KNOBS,
    CASE_PETG_ENCLOSURES,
    CASE_LIGHT_PANEL,
    CASE_ALU_JIG_LID,
    CASE_SIGN_PANELS_REPEAT,
    CASE_LASER_ALU,
    CASE_INCH_GASKETS,
    CASE_CM_STANDOFFS,
    CASE_OUTDOOR_PLA,
    CASE_ENCLOSURES_250,
    CASE_SEALED_MDF,
    CASE_COASTERS_VOLUME,
    CASE_PETG_CLIPS_VOLUME,
    CASE_RESIN_FIXTURES_REPEAT,
    CASE_BRASS_SPACERS,
    CASE_SLA_LAMP_OVERSIZE,
    CASE_PLY_TOY_PARTS,
)

DEV_CASES: tuple[Case, ...] = tuple(c for c in CASES if not c.held_out)
HELD_OUT_CASES: tuple[Case, ...] = tuple(c for c in CASES if c.held_out)


def case_ids(cases: tuple[Case, ...] = CASES) -> list[str]:
    return [c.id for c in cases]


def by_id(case_id: str) -> Case:
    for c in CASES:
        if c.id == case_id:
            return c
    raise KeyError(f"no case '{case_id}'. Known: {', '.join(case_ids())}")


# A duplicate id silently halves your eval set and looks like a plateau.
_seen: set[str] = set()
for _c in CASES:
    if _c.id in _seen:
        raise ValueError(f"duplicate case id: {_c.id}")
    _seen.add(_c.id)
del _seen, _c

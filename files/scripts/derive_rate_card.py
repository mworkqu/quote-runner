"""Derive costing/rate_card.json from a workshop cost model.

    python3 scripts/derive_rate_card.py            # print the derivation
    python3 scripts/derive_rate_card.py --write    # regenerate rate_card.json

WHY THIS EXISTS

The rate card used to contain invented numbers. It now contains derived ones,
and this file is the derivation. Every `rate_per_hour` in the card can be
traced back to a salary, a replacement value, or a line on a utility bill.

The method is lifted from `TEW_Workshop_Cost_Rate_Calculator.xlsx` — Al Tawheed
Engineering Workshop, Doha, Rev.01 June 2026 — which computes a true cost per
hour per division from:

    (direct salaries + end-of-service + iqama/ticket/PPE)
  + (equipment replacement value / asset life)
  + (allocated monthly overhead)
  --------------------------------------------------------
  / productive hours per month

The Doha-specific parameters — 26 working days, 9-hour days with 8 productive,
8.33% end-of-service accrual, 300 QAR/month per head for iqama + air ticket +
PPE — are that workbook's, not invented here.

THREE PLACES THIS DELIBERATELY DEPARTS FROM THE WORKBOOK

1. MARGIN IS ON REVENUE, NOT MARKUP ON COST.
   The workbook computes `selling rate = cost x (1 + 25%)`. That is a 25%
   markup, which is a 20% margin on revenue — the workbook's own label says
   "profit margin" and means markup. `margin_floors` below restate it
   correctly. This is the exact error `test_margin_is_on_revenue_not_markup`
   exists to catch, and finding it in a real workbook is the reason that test
   exists.

2. HEAD OFFICE IS RECOVERED.
   In the workbook, Head Office receives a 0 QAR overhead allocation and is
   marked non-billable, so its ~17,700 QAR/month of staff cost is never
   recovered by any selling rate. Here it goes into the overhead pool and comes
   back through the hourly rates.

3. UTILISATION IS NOT 100%.
   The workbook divides monthly cost by 208 productive hours, i.e. it assumes
   every productive hour is sold. No shop achieves that. `UTILISATION` below
   is the single biggest lever in this file — at 65% the rates are ~50% higher
   than the workbook's, and that difference is the whole margin.

WHAT THIS MEANS FOR overhead_pct_of_direct

It is 0.0, and must stay 0.0. The derived machine rates already absorb rent,
power, depreciation and head office. Adding a further 12% "overhead on direct"
would double-count all of it and quote every job high. The README warns about
this; the workbook is the evidence.
"""

from __future__ import annotations

import argparse
import json
import sys
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path

CARD_PATH = Path(__file__).resolve().parent.parent / "costing" / "rate_card.json"

D = Decimal


def q(value: Decimal, places: str = "0.01") -> Decimal:
    return value.quantize(Decimal(places), rounding=ROUND_HALF_UP)


# ===========================================================================
# 1. ASSUMPTIONS  — straight from the workbook's ASSUMPTIONS sheet
# ===========================================================================

WORKING_DAYS_PER_MONTH = D("26")  # excludes Fridays
WORKING_HOURS_PER_DAY = D("9")  # includes a 1-hour break
PRODUCTIVE_HOURS_PER_DAY = D("8")
PRODUCTIVE_HOURS_PER_MONTH = WORKING_DAYS_PER_MONTH * PRODUCTIVE_HOURS_PER_DAY  # 208

EOS_ACCRUAL_RATE = D("0.0833")  # one month per year
PER_HEAD_MONTHLY = D("300")  # iqama 1200 + ticket 1200 + PPE 400 + medical 800, /12

# The workbook's departure point. 65% is a prototyping shop that is doing well.
UTILISATION = D("0.65")

CURRENCY = "QAR"
CARD_VERSION = "0.2.0-derived-tew"


def staff_monthly_cost(salary: Decimal) -> Decimal:
    """Workbook formula: salary + EOS accrual + fixed per-head allowances."""
    return salary * (D("1") + EOS_ACCRUAL_RATE) + PER_HEAD_MONTHLY


def monthly_depreciation(replacement_value: Decimal, life_years: Decimal) -> Decimal:
    """Straight line, as the workbook's EQUIPMENT sheet does it."""
    return replacement_value / (life_years * D("12"))


# ===========================================================================
# 2. OVERHEAD  — the workbook's categories, scaled to a prototyping shop
# ===========================================================================
#
# The workbook's 98,050 QAR/month is a 26-person heavy workshop with a 50,000
# rent line. A prototyping lab is a fraction of that. Same categories, smaller
# numbers, so the two can be compared line for line.
#
# The pool is SPLIT, because the two halves are recovered differently:
#
#   FACILITY pool  — the cost of having a machine bay: rent, power, insurance,
#                    consumables, maintenance. Recovered through MACHINE rates.
#   PEOPLE pool    — the cost of having a person on staff: IT, admin, head
#                    office, marketing. Recovered through LABOUR rates.
#
# Splitting it is what stops the double-count. Every riyal is in exactly one
# pool and is recovered exactly once.

FACILITY_OVERHEAD = {
    "Workshop rent": D("12000"),
    "Office / reception rent": D("1500"),
    "Electricity — workshop": D("3000"),
    "Electricity — office": D("800"),
    "Water & sewage (KAHRAMAA)": D("300"),
    "Chilled water / air conditioning": D("900"),
    "Compressed air / gas supply": D("400"),
    "Vehicle fuel": D("900"),
    "Vehicle insurance & maintenance": D("400"),
    "Workshop / liability insurance": D("500"),
    "Equipment insurance": D("300"),
    "Legal & audit fees": D("300"),
    "Trade licence & municipality fees": D("400"),
    "Bank charges & finance costs": D("200"),
    "General consumables (nozzles, lenses, bits, abrasives)": D("1200"),
    "Machine maintenance & repairs": D("900"),
    "Calibration of instruments": D("200"),
    "Safety equipment replacement": D("200"),
    "Waste disposal": D("250"),
    "Cleaning & janitorial": D("500"),
    "Security / CCTV": D("300"),
}

PEOPLE_OVERHEAD = {
    "Internet & IT services": D("500"),
    "Telephone & mobile (business)": D("500"),
    "Software licences (CAD / CAM / slicer / ERP)": D("700"),
    "Office stationery & supplies": D("200"),
    "Printing & document costs": D("150"),
    "Postage & courier": D("150"),
    "Marketing & branding": D("300"),
    "Training & certifications": D("200"),
    "Contingency / sundry": D("700"),
}

# Head office, which the workbook never recovers. Salaries at the workbook's
# scale: it pays a General Manager 5,000 and an Office Admin 1,500.
HEAD_OFFICE_STAFF = {
    "Owner / Engineering Manager": D("4000"),
    "Office admin / estimator": D("1500"),
}


# ===========================================================================
# 3. DIVISIONS  — direct staff and equipment
# ===========================================================================
#
# `concurrency` is how many jobs the division can genuinely have in progress at
# once. Three printers running overnight is three concurrent jobs and one
# person; the mill is one job and one person standing at it. This is the same
# physical fact that `feasibility.MACHINE_HOURS_PER_DAY` encodes for lead time,
# applied here to cost recovery.
#
# `hours_per_day` is how long each machine can actually run. Printers go
# overnight. The router does not.

DIVISIONS = {
    "additive": {
        "name": "Additive (FDM / SLA)",
        "concurrency": D("3"),
        "staff": {
            "Print technician": D("1800"),
            "Finishing helper": D("1000"),
        },
        "machines": {
            "fdm_01": {
                "name": "Desktop FDM printer",
                "process": "fdm",
                "replacement_value": D("4000"),
                "life_years": D("5"),
                "hours_per_day": D("20"),
                "setup_minutes": 15,
                "queue_hours": 22,
                "envelope_mm": [250, 210, 220],
                "materials": ["pla", "petg", "abs"],
            },
            "fdm_02": {
                "name": "Large-format engineering FDM",
                "process": "fdm",
                "replacement_value": D("45000"),
                "life_years": D("7"),
                "hours_per_day": D("20"),
                "setup_minutes": 20,
                "queue_hours": 16,
                "envelope_mm": [300, 300, 400],
                "materials": ["pla", "petg", "abs", "pa12_cf"],
            },
            "sla_01": {
                "name": "SLA resin printer + wash/cure",
                "process": "sla",
                "replacement_value": D("18000"),
                "life_years": D("5"),
                "hours_per_day": D("14"),
                "setup_minutes": 20,
                "queue_hours": 12,
                "envelope_mm": [145, 145, 175],
                "materials": ["resin_standard", "resin_tough"],
            },
        },
    },
    "laser_router": {
        "name": "Laser & routing",
        "concurrency": D("1.3"),  # two machines, one operator
        "staff": {
            "Laser / router operator": D("2200"),
            "Workshop helper": D("1000"),
        },
        "machines": {
            "laser_01": {
                "name": "CO2 laser cutter 900x600",
                "process": "laser",
                "replacement_value": D("55000"),
                "life_years": D("8"),
                "hours_per_day": D("8"),
                "setup_minutes": 12,
                "queue_hours": 18,
                "envelope_mm": [900, 600, 25],
                "materials": ["acrylic_3mm", "acrylic_5mm", "mdf_6mm", "ply_9mm"],
            },
            "router_01": {
                "name": "CNC router 1200x600",
                "process": "cnc_router",
                "replacement_value": D("90000"),
                "life_years": D("10"),
                "hours_per_day": D("8"),
                "setup_minutes": 35,
                "queue_hours": 30,
                "envelope_mm": [1200, 600, 80],
                "materials": ["mdf_6mm", "ply_9mm", "acrylic_5mm", "alu_6082"],
            },
        },
    },
    "machining": {
        "name": "CNC machining",
        "concurrency": D("1"),
        "staff": {
            "CNC programmer / operator": D("3000"),
            "Machinist": D("1800"),
        },
        "machines": {
            "mill_01": {
                "name": "3-axis CNC mill",
                "process": "cnc_mill",
                "replacement_value": D("220000"),
                "life_years": D("10"),
                "hours_per_day": D("8"),
                "setup_minutes": 45,
                "queue_hours": 44,
                "envelope_mm": [300, 200, 150],
                "materials": ["alu_6082", "brass_360", "acrylic_5mm"],
                "extra_assets": {"Tooling & metrology": (D("25000"), D("5"))},
            },
        },
    },
}


# ===========================================================================
# 4. LABOUR  — fully absorbed person rates
# ===========================================================================
#
# COST rates, not selling rates. Margin is applied once, at the end, by
# `engine.price_floor`. Putting margin in here would compound it.
#
# Each is one person's monthly cost divided by the hours they actually sell,
# plus their share of the PEOPLE overhead pool.

LABOUR_ROLES = {
    "cad": {"name": "CAD / file prep", "salary": D("3000")},
    "operator": {"name": "Machine operator", "salary": D("2200")},
    "finishing": {"name": "Finishing / QC", "salary": D("1000")},
}


# ===========================================================================
# 5. MATERIALS  — NOT derived; the workbook does not cover consumable stock
# ===========================================================================
#
# These remain estimates, priced at Doha small-quantity stockist rates rather
# than container rates. They are the least defensible numbers left in the card
# and the first thing to replace with real supplier invoices.

MATERIALS = {
    "pla": dict(name="PLA filament", form="mass", cost_per_gram=0.10,
                waste_factor=0.08, in_stock=True, restock_lead_days=7),
    "petg": dict(name="PETG filament", form="mass", cost_per_gram=0.12,
                 waste_factor=0.08, in_stock=True, restock_lead_days=7),
    "abs": dict(name="ABS filament", form="mass", cost_per_gram=0.11,
                waste_factor=0.10, in_stock=True, restock_lead_days=10),
    "pa12_cf": dict(name="PA12 carbon-filled filament", form="mass", cost_per_gram=0.45,
                    waste_factor=0.10, in_stock=False, restock_lead_days=21),
    "resin_standard": dict(name="Standard SLA resin", form="mass", cost_per_gram=0.25,
                           waste_factor=0.12, in_stock=True, restock_lead_days=10),
    "resin_tough": dict(name="Tough SLA resin", form="mass", cost_per_gram=0.38,
                        waste_factor=0.12, in_stock=True, restock_lead_days=14),
    "alu_6082": dict(name="Aluminium 6082 plate/billet", form="mass", cost_per_gram=0.03,
                     waste_factor=0.35, in_stock=True, restock_lead_days=12),
    "brass_360": dict(name="Brass C360 bar", form="mass", cost_per_gram=0.09,
                      waste_factor=0.35, in_stock=False, restock_lead_days=14),
    "acrylic_3mm": dict(name="Cast acrylic 3mm", form="sheet", cost_per_sheet=68.0,
                        sheet_size_mm=[1220, 610, 3], waste_factor=0.0,
                        in_stock=True, restock_lead_days=5),
    "acrylic_5mm": dict(name="Cast acrylic 5mm", form="sheet", cost_per_sheet=100.0,
                        sheet_size_mm=[1220, 610, 5], waste_factor=0.0,
                        in_stock=True, restock_lead_days=5),
    "mdf_6mm": dict(name="MDF 6mm", form="sheet", cost_per_sheet=38.0,
                    sheet_size_mm=[1220, 610, 6], waste_factor=0.0,
                    in_stock=True, restock_lead_days=4),
    "ply_9mm": dict(name="Birch ply 9mm", form="sheet", cost_per_sheet=80.0,
                    sheet_size_mm=[1220, 610, 9], waste_factor=0.0,
                    in_stock=True, restock_lead_days=9),
}


# ===========================================================================
# 6. MARGIN AND SHOP CONSTANTS
# ===========================================================================
#
# The workbook targets a 25% markup on cost. Restated correctly, that is a
# 20% margin on revenue: 0.25 / 1.25 = 0.20. That becomes the THINNEST floor,
# the volume band. The others step up from it.

WORKBOOK_MARKUP = D("0.25")
VOLUME_MARGIN = WORKBOOK_MARKUP / (D("1") + WORKBOOK_MARKUP)  # 0.20 exactly

MARGIN_FLOORS = {
    "standard": D("0.32"),
    "repeat_client": D("0.26"),
    "volume": VOLUME_MARGIN,
}

# Per-job handling only: packaging, courier, the quotation document itself.
# Head-office LABOUR is already in the PEOPLE overhead pool and comes back
# through the labour rates — booking it again here would double-count it.
JOB_ADMIN_COST = D("25")

# Below this the shop should not open a job at all. Derived as roughly one
# attended hour of the most expensive division plus handling, marked up.
MIN_JOB_VALUE = D("300")


# ===========================================================================
# Derivation
# ===========================================================================


def derive(verbose: bool = True) -> dict:
    out = lambda *a: print(*a) if verbose else None
    rule = "─" * 76

    facility_pool = sum(FACILITY_OVERHEAD.values())
    people_pool = sum(PEOPLE_OVERHEAD.values())
    head_office = sum(staff_monthly_cost(s) for s in HEAD_OFFICE_STAFF.values())
    people_pool_total = people_pool + head_office
    total_overhead = facility_pool + people_pool_total

    n_divisions = len(DIVISIONS)
    facility_per_division = facility_pool / n_divisions

    out(f"\n{rule}\n  RATE CARD DERIVATION — method from TEW Workshop Cost Rate Calculator")
    out(f"  {WORKING_DAYS_PER_MONTH} days x {PRODUCTIVE_HOURS_PER_DAY} productive hrs "
        f"= {PRODUCTIVE_HOURS_PER_MONTH} hrs/month, utilisation {UTILISATION:.0%}")
    out(rule)
    out(f"\n  OVERHEAD POOLS (QAR/month)")
    out(f"    facility pool  {float(facility_pool):>12,.2f}   -> machine rates")
    out(f"    people pool    {float(people_pool):>12,.2f}   -> labour rates")
    out(f"    head office    {float(head_office):>12,.2f}   -> labour rates "
        f"(the workbook never recovers this)")
    out(f"    {'':<14} {'─' * 12}")
    out(f"    total          {float(total_overhead):>12,.2f}")
    out(f"\n    facility per division ({n_divisions} divisions, equal split): "
        f"{float(facility_per_division):,.2f}")

    # -- machine rates ------------------------------------------------------
    machines: dict = {}
    out(f"\n{rule}\n  MACHINE RATES\n{rule}")

    for div_key, div in DIVISIONS.items():
        div_staff = sum(staff_monthly_cost(s) for s in div["staff"].values())
        shared_monthly = div_staff + facility_per_division

        attended_hours = PRODUCTIVE_HOURS_PER_MONTH * UTILISATION
        shared_hours = attended_hours * div["concurrency"]
        shared_per_hour = shared_monthly / shared_hours

        out(f"\n  {div['name']}")
        out(f"    direct staff        {float(div_staff):>10,.2f}/mo  "
            f"({', '.join(div['staff'])})")
        out(f"    facility share      {float(facility_per_division):>10,.2f}/mo")
        out(f"    concurrency         {float(div['concurrency']):>10,.2f}  "
            f"-> {float(shared_hours):,.1f} sellable hrs/mo")
        out(f"    shared cost/hour    {float(shared_per_hour):>10,.2f}")

        for m_id, m in div["machines"].items():
            depr = monthly_depreciation(m["replacement_value"], m["life_years"])
            for _, (val, life) in m.get("extra_assets", {}).items():
                depr += monthly_depreciation(val, life)

            machine_hours = m["hours_per_day"] * WORKING_DAYS_PER_MONTH * UTILISATION
            own_per_hour = depr / machine_hours
            rate = q(shared_per_hour + own_per_hour, "1")

            out(f"      {m_id:<12} depr {float(depr):>8,.2f}/mo over "
                f"{float(machine_hours):>6,.1f} hrs = {float(own_per_hour):>5,.2f}"
                f"  +  {float(shared_per_hour):>6,.2f}  =  {float(rate):>6,.2f} QAR/hr")

            machines[m_id] = {
                "name": m["name"],
                "process": m["process"],
                "division": div_key,
                "rate_per_hour": float(rate),
                "setup_minutes": m["setup_minutes"],
                "queue_hours": m["queue_hours"],
                "envelope_mm": m["envelope_mm"],
                "materials": m["materials"],
                "_derivation": {
                    "replacement_value": float(m["replacement_value"]),
                    "life_years": float(m["life_years"]),
                    "monthly_depreciation": float(q(depr)),
                    "billable_hours_per_month": float(q(machine_hours, "0.1")),
                    "shared_cost_per_hour": float(q(shared_per_hour)),
                    "own_cost_per_hour": float(q(own_per_hour)),
                },
            }

    # -- labour rates -------------------------------------------------------
    n_direct_staff = sum(len(d["staff"]) for d in DIVISIONS.values())
    sellable_person_hours = PRODUCTIVE_HOURS_PER_MONTH * UTILISATION * n_direct_staff
    people_per_hour = people_pool_total / sellable_person_hours

    out(f"\n{rule}\n  LABOUR RATES (cost, not selling)\n{rule}")
    out(f"    people pool {float(people_pool_total):,.2f}/mo over "
        f"{n_direct_staff} staff x {float(PRODUCTIVE_HOURS_PER_MONTH * UTILISATION):,.1f} hrs "
        f"= {float(people_per_hour):,.2f}/hr absorbed\n")

    labour: dict = {}
    for key, role in LABOUR_ROLES.items():
        monthly = staff_monthly_cost(role["salary"])
        sellable = PRODUCTIVE_HOURS_PER_MONTH * UTILISATION
        base = monthly / sellable
        rate = q(base + people_per_hour, "1")
        out(f"    {key:<11} salary {float(role['salary']):>6,.0f} -> "
            f"{float(monthly):>8,.2f}/mo / {float(sellable):,.1f} hrs = {float(base):>6,.2f}"
            f"  + {float(people_per_hour):>5,.2f}  =  {float(rate):>6,.2f} QAR/hr")
        labour[key] = {
            "name": role["name"],
            "rate_per_hour": float(rate),
            "_derivation": {
                "monthly_salary": float(role["salary"]),
                "monthly_cost": float(q(monthly)),
                "absorbed_overhead_per_hour": float(q(people_per_hour)),
            },
        }

    out(f"\n{rule}\n  MARGIN\n{rule}")
    out(f"    workbook target      {float(WORKBOOK_MARKUP):.0%} MARKUP on cost")
    out(f"    restated on revenue  {float(VOLUME_MARGIN):.0%}  "
        f"({float(WORKBOOK_MARKUP)} / {float(1 + WORKBOOK_MARKUP)})")
    for band, m in MARGIN_FLOORS.items():
        out(f"      {band:<15} {float(m):.0%} on revenue  "
            f"(= {float(m / (1 - m)):.0%} markup)")
    out(f"\n    overhead_pct_of_direct = 0.0 — the rates above already absorb it.")
    out(f"    min_job_value          = {float(MIN_JOB_VALUE):,.2f}")
    out(f"    job_admin_cost         = {float(JOB_ADMIN_COST):,.2f} (handling only)\n")

    card = {
        "version": CARD_VERSION,
        "currency": CURRENCY,
        "note": (
            "DERIVED, not guessed. Generated by scripts/derive_rate_card.py using the "
            "method in TEW_Workshop_Cost_Rate_Calculator.xlsx (Al Tawheed Engineering "
            "Workshop, Doha, Rev.01 June 2026). Edit the script, not this file — a hand "
            "edit here is lost the next time anyone regenerates. Material costs are the "
            "one thing NOT derived; replace them with supplier invoices first."
        ),
        "derivation": {
            "source": "TEW_Workshop_Cost_Rate_Calculator.xlsx",
            "working_days_per_month": float(WORKING_DAYS_PER_MONTH),
            "productive_hours_per_day": float(PRODUCTIVE_HOURS_PER_DAY),
            "productive_hours_per_month": float(PRODUCTIVE_HOURS_PER_MONTH),
            "utilisation": float(UTILISATION),
            "eos_accrual_rate": float(EOS_ACCRUAL_RATE),
            "per_head_monthly_allowances": float(PER_HEAD_MONTHLY),
            "facility_overhead_monthly": float(q(facility_pool)),
            "people_overhead_monthly": float(q(people_pool_total)),
            "total_overhead_monthly": float(q(total_overhead)),
            "workbook_markup_target": float(WORKBOOK_MARKUP),
            "restated_as_margin_on_revenue": float(q(VOLUME_MARGIN, "0.0001")),
        },
        "overhead_pct_of_direct": 0.0,
        "margin_floors": {k: float(q(v, "0.0001")) for k, v in MARGIN_FLOORS.items()},
        "shop": {
            "min_job_value": float(MIN_JOB_VALUE),
            "job_admin_cost": float(JOB_ADMIN_COST),
        },
        "labour": labour,
        "machines": machines,
        "materials": MATERIALS,
    }
    return card


def main(argv: list[str]) -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    p.add_argument("--write", action="store_true", help="overwrite costing/rate_card.json")
    p.add_argument("--quiet", action="store_true", help="no derivation printout")
    args = p.parse_args(argv)

    card = derive(verbose=not args.quiet)

    if args.write:
        CARD_PATH.write_text(json.dumps(card, indent=2) + "\n", encoding="utf-8")
        print(f"  wrote {CARD_PATH}\n")
        print("  Now run:  python3 -m evals.harness --validate")
        print("            python3 test_costing.py\n")
    else:
        print("  (dry run — pass --write to regenerate costing/rate_card.json)\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))

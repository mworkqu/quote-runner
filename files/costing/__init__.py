"""Quote Runner cost model.

Everything downstream reads from here: the pricing tool the agent calls, the
honest judge GEPA optimises against, and the gameable judge it gets contrasted
with.

The one design decision everything rests on: the model estimates PHYSICAL
QUANTITIES and this package turns them into money. `price_job()` has no
argument for a price, so the agent cannot name one. That constraint lives in a
tool signature rather than in a prompt, because GEPA is allowed to rewrite
prompts and is not allowed to rewrite this.

    from costing import RateCard, Job, cost_job, check_feasibility
    from costing.judge import honest_judge, QuoteUnderTest
"""

from .agent_tools import list_capabilities, price_job
from .engine import CostLine, JobCost, PriceFloor, cost_job, price_floor, sheets_required
from .feasibility import (
    MACHINE_HOURS_PER_DAY,
    Blocker,
    Feasibility,
    check_feasibility,
    fits_envelope,
)
from .judge import QuoteUnderTest, Verdict, gameable_judge, honest_judge
from .models import (
    CLIENT_BANDS,
    D,
    Job,
    LabourRate,
    Machine,
    Material,
    Operation,
    RateCard,
    RateCardError,
    money,
)

__all__ = [
    # models
    "RateCard",
    "RateCardError",
    "Machine",
    "Material",
    "LabourRate",
    "Operation",
    "Job",
    "CLIENT_BANDS",
    "D",
    "money",
    # engine
    "cost_job",
    "price_floor",
    "sheets_required",
    "CostLine",
    "JobCost",
    "PriceFloor",
    # feasibility
    "check_feasibility",
    "fits_envelope",
    "Feasibility",
    "Blocker",
    "MACHINE_HOURS_PER_DAY",
    # judge
    "honest_judge",
    "gameable_judge",
    "QuoteUnderTest",
    "Verdict",
    # tools
    "list_capabilities",
    "price_job",
]

__version__ = "0.1.0"

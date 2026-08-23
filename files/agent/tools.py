"""Tools exposed to the LLM.

DESIGN NOTE — why `operations_json` is a string rather than a typed list:

Typing it as `list[OperationInput]` produces a lovely JSON schema with `$defs`
and `$ref`. Gemini function-calling on Vertex has historically rejected `$ref`,
and the failure mode is a 400 at request time, not at build time. Primitives
only means the declaration is valid everywhere, at the cost of the model writing
a JSON string. Gemini does that reliably.

The typed API still exists in `costing.agent_tools.price_job` and the tests use
it. This module is only the LLM's doorway.
"""

from __future__ import annotations

import json
from typing import Any

from costing.agent_tools import list_capabilities as _list_capabilities
from costing.agent_tools import price_job as _price_job

OPERATION_KEYS = """Each operation object may contain:
  machine_id                 (required) id from list_capabilities
  material_id                (required) id from list_capabilities
  machine_minutes_per_unit   (required) machine time for ONE unit
  material_grams_per_unit    mass-form materials (filament, resin, metal)
  support_grams_per_unit     printed support material
  parts_per_sheet            sheet-form materials (acrylic, ply, MDF)
  cad_minutes                file prep, charged ONCE for the whole job
  operator_minutes_per_unit  attended machine time per unit
  finishing_minutes_per_unit sanding, washing, curing, QC per unit
  part_bbox_mm               [x, y, z] overall size. Omit ONLY if the enquiry
                             genuinely does not establish dimensions."""


def list_capabilities() -> dict[str, Any]:
    """List the machines and materials this workshop actually has.

    Call this FIRST, before pricing anything. Machine ids, material ids, build
    envelopes, current stock levels and restock lead times all come from here.
    Never invent an id.
    """
    return _list_capabilities()


def price_job(
    quantity: int,
    operations_json: str,
    client_band: str = "standard",
    due_in_days: int = -1,
) -> dict[str, Any]:
    """Cost a planned job and get the minimum viable price.

    You supply PHYSICAL ESTIMATES. This tool returns money. There is no
    argument for a price because you do not decide the price.

    Args:
        quantity: how many units the client wants.
        operations_json: a JSON array string, one object per machine pass.
            {OPERATION_KEYS}
            Example:
            '[{{"machine_id":"fdm_01","material_id":"pla",
               "machine_minutes_per_unit":95,"material_grams_per_unit":42,
               "cad_minutes":20,"part_bbox_mm":[120,60,35]}}]'
        client_band: "standard", "repeat_client", or "volume".
        due_in_days: client's deadline in days. Use -1 if none was stated.

    Returns:
        Itemised cost, price_floor (the MINIMUM viable price), estimated
        lead days, and blockers explaining why the job cannot be delivered.
    """
    try:
        operations = json.loads(operations_json)
    except json.JSONDecodeError as e:
        return {
            "error": "operations_json is not valid JSON",
            "detail": str(e),
            "hint": "Send a JSON array string. " + OPERATION_KEYS,
        }

    if not isinstance(operations, list) or not operations:
        return {
            "error": "operations_json must be a non-empty JSON array",
            "hint": OPERATION_KEYS,
        }

    for op in operations:
        if not isinstance(op, dict):
            return {"error": "each operation must be a JSON object", "hint": OPERATION_KEYS}
        if "machine_id" not in op or "material_id" not in op:
            return {
                "error": "each operation needs machine_id and material_id",
                "hint": "Call list_capabilities() for valid ids.",
            }

    return _price_job(
        quantity=quantity,
        operations=operations,
        client_band=client_band,
        due_in_days=None if due_in_days is None or due_in_days < 0 else due_in_days,
    )


# The docstring interpolation has to happen after definition, because the
# Args block is what the model reads as the parameter description.
price_job.__doc__ = price_job.__doc__.replace("{OPERATION_KEYS}", OPERATION_KEYS).replace(
    "{{", "{"
).replace("}}", "}")

TOOLS = [list_capabilities, price_job]

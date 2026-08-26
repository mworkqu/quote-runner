"""The customer-facing web layer. A new layer, not a new agent.

Everything here is a READER of things that already exist. It runs the same
`QuoteRunnerAgent.quote_async` the eval harness and `POST /quote` run, then
reads the ADK tool events that run emitted. It does not price anything, does
not decide anything, and imports nothing from `costing/` except the rate card
loader for the currency symbol.

THE PRICE GATE
--------------
`price_job()` returns `price_floor` -- the MINIMUM viable price. The agent then
names the final figure at or above it. So the headline number is the agent's,
and it is only allowed onto the screen after `_resolve()` has checked it
against the engine's floor from that same run. Four conditions send it to an
error state instead of a price:

    parse_error           the reply was not readable JSON
    priced_without_tool   a figure was named without ever calling price_job
    no engine output      price_job never returned a costed result
    price < price_floor   the agent undercut the engine

Everything else on screen -- every cost line, hour, day count, blocker message,
machine envelope and the currency itself -- is engine output reproduced
verbatim, never model prose.
"""

from __future__ import annotations

import asyncio
import itertools
import json
import logging
import os
import threading
import traceback
from pathlib import Path
from typing import Any

from fastapi import APIRouter
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel, Field

log = logging.getLogger("quote_runner.web")

WEB_DIR = Path(__file__).with_name("web")

# The agent makes two Vertex round trips plus tool calls. 180s is generous
# against a Cloud Run request timeout of 600 (see deploy.sh).
QUOTE_TIMEOUT_S = float(os.environ.get("QR_QUOTE_TIMEOUT", "180"))

router = APIRouter()


# ---------------------------------------------------------------------------
# Shop identity
# ---------------------------------------------------------------------------


def _meta() -> dict[str, Any]:
    """Currency, rate card version, model and revision for the page header.

    These four fields also appear in GET /healthz, but this is a deliberate
    second source rather than a call through to it. /healthz is the Cloud Run
    health check and stays exactly as it is; deploy.sh additionally claims it is
    intercepted at the Google edge on *.run.app and never reaches the container.
    Whether or not that holds, a header that renders blank in the demo reads as
    a broken app, so the UI reads its identity from a path under /api/ that this
    module owns outright.

    The currency here is only ever a header caption. The currency attached to a
    quote comes from the engine's own response inside `_resolve`.
    """
    from costing import RateCard

    card = RateCard.load()
    return {
        "currency": card.currency,
        "rate_card_version": card.version,
        "model": os.environ.get("QR_MODEL", "gemini-3.5-flash"),
        "revision": os.environ.get("K_REVISION", "local"),
    }


# ---------------------------------------------------------------------------
# Quote references
# ---------------------------------------------------------------------------

_counter = itertools.count(1)
_counter_lock = threading.Lock()


def _next_quote_ref() -> str:
    """Process-local counter. A container restart resets it to QR-0001.

    Deliberately not persisted. This service has no datastore, and adding one
    so a demo reference number survives a cold start would buy a dependency and
    an IAM grant for nothing anyone watching can see.
    """
    with _counter_lock:
        return f"QR-{next(_counter):04d}"


# ---------------------------------------------------------------------------
# Reading the captured ADK tool events
# ---------------------------------------------------------------------------


def _tool_response(event: dict[str, Any]) -> dict[str, Any] | None:
    """The tool's return value as the engine produced it.

    ADK hands a dict-returning tool its dict straight back. It wraps anything
    else as `{"result": ...}`, so unwrap that one shape defensively -- no
    engine response is a bare single `result` key, so this cannot misfire.
    """
    response = event.get("response")
    if not isinstance(response, dict):
        return None
    if set(response) == {"result"} and isinstance(response["result"], dict):
        return response["result"]
    return response


def _last_good(events: list[dict], name: str) -> tuple[dict | None, dict | None]:
    """The most recent call to `name` that came back without a structured error.

    Most recent, not first: a model that guesses `machine_id="cnc"` gets an
    error dict plus a hint and fixes itself on the next call inside the same
    turn. The corrected call is the one that costed the job.
    """
    for event in reversed(events):
        if event.get("name") != name:
            continue
        response = _tool_response(event)
        if response is None or "error" in response:
            continue
        return event, response
    return None, None


def _select_price_job(
    events: list[dict], escalated: bool
) -> tuple[dict | None, dict | None]:
    """Which costed call does the agent's decision actually rest on?

    Agents shop around. A real run of the oversize manifold enquiry costed it
    twice -- once on router_01, where it fits, then once on mill_01, where it
    does not -- and both calls succeeded. "Last good call wins" would have
    picked whichever happened to come second, which is a coin flip, not a
    reading of what the agent did.

    So match the call to the decision. A quote can only have come from a call
    that came back deliverable; a refusal is evidenced by one that did not.
    Fall back to the most recent costed call when no candidate matches, which
    then meets the gates in `_resolve` on its own merits -- an agent that
    quoted with nothing deliverable behind it still gets refused.
    """
    costed = [
        (event, response)
        for event in events
        if event.get("name") == "price_job"
        and (response := _tool_response(event)) is not None
        and "error" not in response
    ]
    if not costed:
        return None, None

    wanted = (lambda r: not r.get("deliverable")) if escalated else (
        lambda r: bool(r.get("deliverable"))
    )
    for event, response in reversed(costed):
        if wanted(response):
            return event, response
    return costed[-1]


# A next step for the client when the agent supplied no question of its own.
# UI copy keyed off the engine's blocker code -- the reason above it is always
# the engine's own message, reproduced verbatim.
_ACTION_BY_CODE = {
    "envelope_exceeded": "Confirm whether the part can be split into sections, "
    "or supply a revised design that fits the machine envelope.",
    "dimensions_unknown": "Send the part's overall dimensions, or a drawing or "
    "3D model we can measure.",
    "incompatible_material": "Choose a material the machine runs, or confirm a "
    "different process is acceptable.",
    "lead_time_exceeded": "Confirm whether the deadline can move, or reduce the "
    "quantity so it fits the window.",
}


def _recommended_action(question: str, engine: dict[str, Any] | None) -> str:
    if question:
        return question
    for blocker in (engine or {}).get("blockers", []):
        if action := _ACTION_BY_CODE.get(blocker.get("code", "")):
            return action
    return ""


def _operations(args: dict[str, Any]) -> list[dict[str, Any]]:
    """The operations the model planned.

    `operations_json` reaches the tool as a JSON STRING by design -- typing it
    would emit $defs/$ref and Vertex function-calling rejects those at request
    time. See agent/tools.py. So it is parsed back out here, not "cleaned up"
    upstream.
    """
    raw = args.get("operations_json")
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except json.JSONDecodeError:
            return []
    if not isinstance(raw, list):
        return []
    return [op for op in raw if isinstance(op, dict)]


def _num(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _hours(value: Any) -> str:
    return f"{_num(value):.1f}h"


# ---------------------------------------------------------------------------
# Agent activity -> the checklist
# ---------------------------------------------------------------------------
#
# The top-level rows are the tool calls that actually happened, one row per
# call, in order, with their real durations. The feasibility sub-checks nest
# under price_job because that is literally where they run -- inside
# costing.feasibility.check_feasibility, during that one call. They are not
# separate agent decisions and are not drawn as though they were.


def _blockers_by_code(engine: dict[str, Any], codes: set[str]) -> list[str]:
    return [
        blocker.get("message", "")
        for blocker in engine.get("blockers", [])
        if blocker.get("code") in codes
    ]


_ENVELOPE_CODES = {"envelope_exceeded", "dimensions_unknown"}
_MATERIAL_CODES = {"incompatible_material"}


def _price_job_checks(
    engine: dict[str, Any],
    operations: list[dict[str, Any]],
    machines: dict[str, Any],
    materials: dict[str, Any],
    withhold_money: bool,
) -> list[dict[str, Any]]:
    """Sub-checks under one price_job call. Every line is engine output.

    `withhold_money` blanks the figures in the price-floor check whenever the
    run did not end in a quote. The costing engine still ran and the check
    still appears -- suppressing it entirely would misreport what happened --
    but a refused enquiry must not put a currency figure on screen anywhere,
    and an agent that costs a job on three machines before refusing would
    otherwise leak three price floors into the activity panel.
    """
    currency = engine.get("currency", "")
    checks: list[dict[str, Any]] = []

    # -- machine capability --------------------------------------------------
    lines = []
    for op in operations:
        machine_id = str(op.get("machine_id", ""))
        machine = machines.get(machine_id)
        if machine:
            envelope = "x".join(f"{_num(v):g}" for v in machine.get("build_envelope_mm", []))
            lines.append(f"{machine_id} — {machine.get('name', '')} — envelope {envelope}mm")
        else:
            lines.append(f"{machine_id} — not in the capability list")
    faults = _blockers_by_code(engine, _ENVELOPE_CODES)
    checks.append(
        {
            "label": "machine capability",
            "status": "blocked" if faults else "ok",
            "lines": lines + faults,
        }
    )

    # -- material availability ----------------------------------------------
    lines = []
    for op in operations:
        material_id = str(op.get("material_id", ""))
        material = materials.get(material_id)
        if not material:
            lines.append(f"{material_id} — not in the capability list")
            continue
        if material.get("in_stock"):
            stock = "in stock"
        else:
            stock = f"out of stock — {material.get('restock_lead_days')}d restock"
        lines.append(f"{material_id} — {material.get('name', '')} — {stock}")
    procurement = int(_num(engine.get("lead_time_breakdown", {}).get("procurement_days")))
    if procurement:
        lines.append(f"procurement runs before the queue — {procurement}d")
    faults = _blockers_by_code(engine, _MATERIAL_CODES)
    checks.append(
        {
            "label": "material availability",
            "status": "blocked" if faults else ("warn" if procurement else "ok"),
            "lines": lines + faults,
        }
    )

    # -- machining time ------------------------------------------------------
    breakdown = engine.get("lead_time_breakdown", {})
    checks.append(
        {
            "label": "machining time",
            "status": "ok",
            "lines": [
                f"run {_hours(breakdown.get('run_hours'))} + "
                f"post-process {_hours(breakdown.get('post_process_hours'))}",
                f"machine queue {_hours(breakdown.get('queue_hours'))} at "
                f"{_num(breakdown.get('machine_hours_per_day')):g} machine hours/day",
            ],
        }
    )

    # -- price ---------------------------------------------------------------
    cost = engine.get("cost", {})
    if withhold_money:
        lines = ["job costed — figures withheld, no quote issued"]
    else:
        lines = [
            f"total cost {currency} {_num(cost.get('total_cost')):,.2f} — "
            f"margin floor {_num(engine.get('margin_floor')) * 100:.0f}% on revenue",
            f"price floor {currency} {_num(engine.get('price_floor')):,.2f} "
            f"({currency} {_num(engine.get('price_floor_per_unit')):,.2f} per unit)",
        ]
        if engine.get("min_job_value_applied"):
            lines.append("minimum job value binds here, not the margin")
    checks.append({"label": "price floor", "status": "ok", "lines": lines})

    # -- feasibility verdict -------------------------------------------------
    other = [
        blocker.get("message", "")
        for blocker in engine.get("blockers", [])
        if blocker.get("code") not in _ENVELOPE_CODES | _MATERIAL_CODES
    ]
    if engine.get("deliverable"):
        verdict = [f"deliverable — estimated lead time {engine.get('estimated_lead_days')} days"]
        status = "ok"
    else:
        verdict = ["DO NOT QUOTE — not deliverable as specified"]
        status = "blocked"
    checks.append({"label": "feasibility", "status": status, "lines": verdict + other})

    return checks


def _build_steps(
    events: list[dict[str, Any]],
    machines: dict[str, Any],
    materials: dict[str, Any],
    withhold_money: bool = False,
) -> list[dict[str, Any]]:
    steps: list[dict[str, Any]] = []
    for event in events:
        name = event.get("name", "")
        response = _tool_response(event) or {}
        step: dict[str, Any] = {
            "name": name,
            "duration_ms": event.get("duration_ms"),
            "started_ms": event.get("started_ms"),
            "status": "error" if "error" in response else "ok",
            "detail": "",
            "checks": [],
        }

        if "error" in response:
            # A rejected call is real and is shown. The model normally corrects
            # itself on the next call, which appears as its own row.
            step["detail"] = str(response.get("error", ""))
        elif name == "list_capabilities":
            step["detail"] = (
                f"{len(response.get('machines', []))} machines, "
                f"{len(response.get('materials', []))} materials — "
                f"rate card {response.get('rate_card_version', '')}"
            )
        elif name == "price_job":
            operations = _operations(event.get("args") or {})
            step["detail"] = (
                f"quantity {response.get('quantity')} — "
                f"{len(operations)} operation{'s' if len(operations) != 1 else ''} — "
                f"{response.get('client_band', '')}"
            )
            step["checks"] = _price_job_checks(
                response, operations, machines, materials, withhold_money
            )

        steps.append(step)
    return steps


# ---------------------------------------------------------------------------
# Outcome resolution -- the price gate
# ---------------------------------------------------------------------------


def _error(kind: str, title: str, message: str) -> dict[str, Any]:
    return {"kind": kind, "title": title, "message": message}


def _quote_payload(
    out: dict[str, Any],
    engine: dict[str, Any],
    operations: list[dict[str, Any]],
    machines: dict[str, Any],
    materials: dict[str, Any],
) -> dict[str, Any]:
    currency = engine.get("currency", "")
    floor = _num(engine.get("price_floor"))
    price = _num(out.get("price"))
    breakdown = engine.get("lead_time_breakdown", {})
    cost = engine.get("cost", {})

    process_names = []
    material_names = []
    for op in operations:
        machine = machines.get(str(op.get("machine_id", "")))
        if machine:
            process_names.append(f"{machine.get('name', '')} ({machine.get('process', '')})")
        material = materials.get(str(op.get("material_id", "")))
        if material:
            material_names.append(str(material.get("name", "")))

    margin_amount = floor - _num(cost.get("total_cost"))

    return {
        "currency": currency,
        "price": price,
        "price_floor": floor,
        # Rendered on screen directly under the headline, not buried in the
        # breakdown: it is the visible proof that the engine bounded the model.
        "above_floor_pct": ((price - floor) / floor * 100) if floor else 0.0,
        "quantity": engine.get("quantity"),
        "process": " + ".join(dict.fromkeys(process_names)),
        "material": " + ".join(dict.fromkeys(material_names)),
        "production_hours": _num(breakdown.get("run_hours"))
        + _num(breakdown.get("post_process_hours")),
        "lead_days": engine.get("estimated_lead_days"),
        "promised_lead_days": out.get("promised_lead_days"),
        "client_band": engine.get("client_band"),
        "cost_lines": cost.get("lines", []),
        "cost_totals": {
            "machine_cost": _num(cost.get("machine_cost")),
            "material_cost": _num(cost.get("material_cost")),
            "labour_cost": _num(cost.get("labour_cost")),
            "direct_cost": _num(cost.get("direct_cost")),
            "overhead_cost": _num(cost.get("overhead_cost")),
            "admin_cost": _num(cost.get("admin_cost")),
            "total_cost": _num(cost.get("total_cost")),
            "per_unit_cost": _num(cost.get("per_unit_cost")),
        },
        "margin_floor": _num(engine.get("margin_floor")),
        "margin_amount": margin_amount,
        "min_job_value_applied": bool(engine.get("min_job_value_applied")),
        "reasoning": out.get("reasoning") or "",
    }


def _resolve(out: dict[str, Any], currency_fallback: str) -> dict[str, Any]:
    """Turn one agent run into exactly one of: quote, refusal, error.

    The outcome is decided BEFORE the activity steps are drawn, because the
    steps have to know it: a run that ends in a refusal must not print a
    currency figure anywhere, including in the engine's own working.
    """
    events = out.get("tool_events") or []
    _, capabilities = _last_good(events, "list_capabilities")
    capabilities = capabilities or {}
    machines = {m.get("machine_id"): m for m in capabilities.get("machines", [])}
    materials = {m.get("material_id"): m for m in capabilities.get("materials", [])}

    escalated = bool(out.get("escalated"))
    price_event, engine = _select_price_job(events, escalated)
    operations = _operations((price_event or {}).get("args") or {})

    currency = (
        (engine or {}).get("currency") or capabilities.get("currency") or currency_fallback
    )
    payload: dict[str, Any] = {
        "currency": currency,
        "elapsed_ms": out.get("elapsed_ms"),
        "tool_calls": out.get("tool_calls", []),
    }

    def done() -> dict[str, Any]:
        payload["steps"] = _build_steps(
            events, machines, materials, withhold_money=payload["outcome"] != "quote"
        )
        return payload

    # -- gate 1: was the reply even readable? --------------------------------
    if out.get("parse_error"):
        payload["outcome"] = "error"
        payload["error"] = _error(
            "parse_error",
            "Agent reply could not be read",
            "The model did not return readable JSON, so no quote was produced. "
            "An unparseable reply is recorded as a failure, never as a cheap quote.",
        )
        return done()

    # -- gate 2: a figure named without the engine ---------------------------
    # Its own state, with its own words. This is the exact failure the whole
    # architecture exists to prevent, so if it ever fires it must be legible
    # rather than a generic error. An escalation that never reached price_job
    # is not this -- no figure was named -- and falls through to the refusal.
    if out.get("priced_without_tool") and not escalated:
        payload["outcome"] = "error"
        payload["error"] = _error(
            "priced_without_tool",
            "Quote blocked — costing engine was never called",
            "The agent attempted to price this job without calling the costing "
            "engine. No price produced this way can be shown.",
        )
        return done()

    # -- gate 3: did the engine return a costed result? ----------------------
    if engine is None:
        if escalated:
            payload["outcome"] = "refusal"
            payload["refusal"] = {
                "headline": "Unable to quote",
                "reasons": [],
                "recommended_action": _recommended_action(out.get("question") or "", None),
                "reasoning": out.get("reasoning") or "",
                "engine_consulted": False,
            }
            return done()
        payload["outcome"] = "error"
        payload["error"] = _error(
            "no_engine_output",
            "Costing engine returned no result",
            "The run finished without a successful price_job call, so there is "
            "no costed result to show.",
        )
        return done()

    # -- gate 4: the engine's own verdict outranks the agent's ---------------
    if not engine.get("deliverable") or escalated:
        payload["outcome"] = "refusal"
        payload["refusal"] = {
            "headline": "Unable to quote",
            "reasons": [b.get("message", "") for b in engine.get("blockers", [])],
            "recommended_action": _recommended_action(out.get("question") or "", engine),
            "reasoning": out.get("reasoning") or "",
            "engine_consulted": True,
            # The engine said undeliverable and the agent named a figure anyway.
            # The figure is discarded; this flag makes the disagreement visible.
            "agent_quoted_against_blockers": bool(
                not engine.get("deliverable") and not escalated and _num(out.get("price")) > 0
            ),
        }
        return done()

    # -- gate 5: the price itself --------------------------------------------
    floor = _num(engine.get("price_floor"))
    price = _num(out.get("price"))
    if price <= 0:
        payload["outcome"] = "error"
        payload["error"] = _error(
            "no_price",
            "No price returned",
            "The job costed cleanly but the agent named no figure, so there is "
            "nothing to quote.",
        )
        return done()
    if price < floor:
        payload["outcome"] = "error"
        payload["error"] = _error(
            "below_floor",
            "Quote blocked — below the engine's price floor",
            "The agent returned a figure below the costing engine's price "
            "floor. A price under the floor loses the shop money on the job, "
            "so it is not shown.",
        )
        return done()

    payload["outcome"] = "quote"
    payload["quote"] = _quote_payload(out, engine, operations, machines, materials)
    return done()


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


class QuoteRequest(BaseModel):
    request: str = Field(min_length=1, max_length=4000)
    # Filenames only, exactly as POST /quote already accepts them. This is not
    # file upload -- it lets the "no dimensions" example carry the same photo
    # filename its eval case carries, so the demo matches the eval.
    attachments: list[str] = Field(default_factory=list, max_length=10)


def _file(name: str, media_type: str) -> FileResponse:
    return FileResponse(WEB_DIR / name, media_type=media_type)


@router.get("/", include_in_schema=False)
def index() -> FileResponse:
    return _file("index.html", "text/html; charset=utf-8")


@router.get("/app.css", include_in_schema=False)
def stylesheet() -> FileResponse:
    return _file("app.css", "text/css; charset=utf-8")


@router.get("/app.js", include_in_schema=False)
def script() -> FileResponse:
    return _file("app.js", "text/javascript; charset=utf-8")


@router.get("/api/meta")
def api_meta() -> dict[str, Any]:
    """Shop identity for the page header, fetched on load.

    Separate from /api/quote so the header is populated before the first
    enquiry rather than after it.
    """
    return _meta()


@router.post("/api/quote")
async def api_quote(req: QuoteRequest) -> JSONResponse:
    from costing import RateCard

    from server import agent  # the same singleton POST /quote uses

    currency_fallback = RateCard.load().currency

    try:
        out = await asyncio.wait_for(
            agent().quote_async(req.request, req.attachments),
            timeout=QUOTE_TIMEOUT_S,
        )
    except asyncio.TimeoutError:
        log.error("agent timed out after %ss", QUOTE_TIMEOUT_S)
        return JSONResponse(
            status_code=504,
            content={
                "outcome": "error",
                "currency": currency_fallback,
                "meta": _meta(),
                "steps": [],
                "error": _error(
                    "timeout",
                    "Agent timed out",
                    f"The agent did not finish within {QUOTE_TIMEOUT_S:.0f} seconds.",
                ),
            },
        )
    except Exception as exc:  # noqa: BLE001 - the UI must never get a blank screen
        log.error("agent run failed:\n%s", traceback.format_exc())
        return JSONResponse(
            status_code=500,
            content={
                "outcome": "error",
                "currency": currency_fallback,
                "meta": _meta(),
                "steps": [],
                "error": _error(
                    "exception",
                    "Agent run failed",
                    f"{type(exc).__name__} while running the agent. The full "
                    f"traceback is in the server log.",
                ),
            },
        )

    try:
        payload = _resolve(out, currency_fallback)
    except Exception:  # noqa: BLE001
        log.error("could not resolve agent output:\n%s", traceback.format_exc())
        return JSONResponse(
            status_code=500,
            content={
                "outcome": "error",
                "currency": currency_fallback,
                "meta": _meta(),
                "steps": [],
                "error": _error(
                    "resolve_failed",
                    "Could not read the agent result",
                    "The agent ran but its output could not be resolved into a "
                    "quote. The full traceback is in the server log.",
                ),
            },
        )

    payload["quote_ref"] = _next_quote_ref()
    payload["request"] = req.request
    payload["attachments"] = req.attachments
    payload["meta"] = _meta()
    return JSONResponse(content=payload)

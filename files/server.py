"""Cloud Run service.

Three endpoints:
  GET  /healthz  liveness, and a cheap way to prove the container is on GCP
  POST /quote    one enquiry in, one quote out
  POST /eval     run the whole eval set and return the honest score

/eval is here for the demo video. Hitting it against the deployed URL and
watching the honest score come back while Cloud Trace fills up is the single
clearest "this really runs on Google Cloud" shot you can film.
"""

from __future__ import annotations

import os
from typing import Any

from fastapi import FastAPI
from pydantic import BaseModel

app = FastAPI(title="Quote Runner")

_AGENT = None


def agent():
    global _AGENT
    if _AGENT is None:
        from agent import QuoteRunnerAgent

        _AGENT = QuoteRunnerAgent()
    return _AGENT


def _init_tracing() -> str:
    """Export ADK spans to Cloud Trace when running on GCP."""
    project = os.environ.get("GOOGLE_CLOUD_PROJECT")
    if not project or os.environ.get("QR_DISABLE_TRACE"):
        return "disabled"
    try:
        from opentelemetry import trace
        from opentelemetry.exporter.cloud_trace import CloudTraceSpanExporter
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import BatchSpanProcessor

        provider = TracerProvider()
        provider.add_span_processor(BatchSpanProcessor(CloudTraceSpanExporter(project_id=project)))
        trace.set_tracer_provider(provider)
        return f"cloud-trace:{project}"
    except Exception as e:  # pragma: no cover
        return f"unavailable: {type(e).__name__}"


TRACING = _init_tracing()


class Enquiry(BaseModel):
    enquiry: str
    attachments: list[str] = []


@app.get("/healthz")
def healthz() -> dict[str, Any]:
    from costing import RateCard

    card = RateCard.load()
    return {
        "status": "ok",
        "model": os.environ.get("QR_MODEL", "gemini-3.5-flash"),
        "project": os.environ.get("GOOGLE_CLOUD_PROJECT"),
        "region": os.environ.get("GOOGLE_CLOUD_LOCATION"),
        "vertex": os.environ.get("GOOGLE_GENAI_USE_VERTEXAI"),
        "revision": os.environ.get("K_REVISION", "local"),
        "tracing": TRACING,
        "rate_card_version": card.version,
        "currency": card.currency,
    }


@app.post("/quote")
async def quote(req: Enquiry) -> dict[str, Any]:
    return await agent().quote_async(req.enquiry, req.attachments)


@app.post("/eval")
async def run_eval() -> dict[str, Any]:
    from evals.cases import CASES
    from evals.harness import score_case
    from costing.judge import QuoteUnderTest
    from decimal import Decimal

    rows = []
    a = agent()
    for case in CASES:
        out = await a.quote_async(case.enquiry, case.attachments)
        rows.append(
            score_case(
                case,
                QuoteUnderTest(
                    price=Decimal(str(out.get("price", 0))),
                    promised_lead_days=out.get("promised_lead_days"),
                    escalated=bool(out.get("escalated", False)),
                ),
            )
        )

    passed = sum(r["passed"] for r in rows)
    return {
        "n_cases": len(rows),
        "n_passed": passed,
        "honest_score": round(passed / len(rows), 3),
        "revision": os.environ.get("K_REVISION", "local"),
        "results": rows,
    }

"""Vertex preflight. RUN THIS BEFORE ANYTHING ELSE.

    python3 scripts/verify_vertex.py

Checks, in order of how likely each is to eat your weekend:

  1. env vars set
  2. credentials resolve
  3. the model id exists in your region      <- most common failure
  4. function calling actually works          <- second most common
  5. the agent completes one real enquiry end to end

Failing fast here on Saturday costs ten minutes. Discovering it on Thursday
costs the submission.
"""

from __future__ import annotations

import os
import sys
import traceback

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

OK, BAD = "  [ok]  ", "  [FAIL]"


def main() -> int:
    project = os.environ.get("GOOGLE_CLOUD_PROJECT")
    location = os.environ.get("GOOGLE_CLOUD_LOCATION")
    use_vertex = os.environ.get("GOOGLE_GENAI_USE_VERTEXAI")
    model = os.environ.get("QR_MODEL", "gemini-3.5-flash")

    print("\n1. Environment")
    missing = [
        k
        for k, v in {
            "GOOGLE_CLOUD_PROJECT": project,
            "GOOGLE_CLOUD_LOCATION": location,
            "GOOGLE_GENAI_USE_VERTEXAI": use_vertex,
        }.items()
        if not v
    ]
    if missing:
        print(f"{BAD} missing: {', '.join(missing)}")
        print("\n    export GOOGLE_CLOUD_PROJECT=your-project-id")
        print("    export GOOGLE_CLOUD_LOCATION=us-central1")
        print("    export GOOGLE_GENAI_USE_VERTEXAI=TRUE\n")
        return 1
    print(f"{OK} project={project} location={location} model={model}")

    print("\n2. Credentials")
    try:
        import google.auth

        creds, detected = google.auth.default()
        print(f"{OK} resolved (project={detected})")
    except Exception as e:
        print(f"{BAD} {type(e).__name__}: {e}")
        print("\n    gcloud auth application-default login\n")
        return 1

    print("\n3. Model reachable in region")
    try:
        from google import genai

        client = genai.Client(vertexai=True, project=project, location=location)
        r = client.models.generate_content(model=model, contents="Reply with exactly: pong")
        print(f"{OK} {model} responded: {r.text.strip()[:40]!r}")
    except Exception as e:
        print(f"{BAD} {type(e).__name__}: {str(e)[:300]}")
        print(f"\n    '{model}' may not exist in {location}. Try another region,")
        print("    or check the exact model id in the Vertex Model Garden.\n")
        return 1

    print("\n4. Function calling")
    try:
        from google.genai import types

        def add(a: int, b: int) -> int:
            """Add two numbers."""
            return a + b

        r = client.models.generate_content(
            model=model,
            contents="What is 17 plus 25? Use the tool.",
            config=types.GenerateContentConfig(tools=[add]),
        )
        print(f"{OK} tool loop completed: {r.text.strip()[:60]!r}")
    except Exception as e:
        print(f"{BAD} {type(e).__name__}: {str(e)[:300]}")
        return 1

    print("\n5. Full agent on one enquiry")
    try:
        from agent import QuoteRunnerAgent

        out = QuoteRunnerAgent().quote(
            "Hi, need a mounting bracket printed in PLA, about 120x60x35mm, "
            "just one to test fit. Not urgent."
        )
        print(f"{OK} tools called: {out.get('tool_calls')}")
        print(f"         escalate={out.get('escalated')} price={out.get('price')} "
              f"lead={out.get('promised_lead_days')}")
        if out.get("parse_error"):
            print("         note: reply did not parse as JSON — tighten the prompt")
        if out.get("priced_without_tool") and not out.get("escalated"):
            print("         note: quoted WITHOUT calling price_job — it guessed")
    except Exception:
        print(f"{BAD}")
        traceback.print_exc()
        return 1

    print("\n  All checks passed. Vertex is not going to be your problem.\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

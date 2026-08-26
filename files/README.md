# Quote Runner — cost model

Day 0 of the build. Everything downstream reads from here: the pricing tool the
agent calls, the honest judge GEPA optimises against, and the gameable judge it
gets contrasted with.

```
costing/
  rate_card.json    GENERATED — every number derived by scripts/derive_rate_card.py
  models.py         shapes — dataclasses, rate card loader
  engine.py         physical quantities -> money
  feasibility.py    envelope / stock / lead time
  judge.py          honest_judge (conjunctive) + gameable_judge (deliberately broken)
  agent_tools.py    the ADK tool surface
scripts/
  derive_rate_card.py   the workshop cost model rate_card.json is generated from
demo.py             four worked cases + the reward-hacking contrast
test_costing.py     16 golden tests
```

```bash
python3 demo.py
python3 test_costing.py
```

## The one design decision everything rests on

The model estimates **physical quantities** — grams, machine minutes, parts per
sheet, bounding box. This package turns those into money. `price_job()` has no
argument for a price, so the agent cannot name one. That constraint lives in a
tool signature, not in a prompt, because GEPA is allowed to rewrite prompts and
is not allowed to rewrite this.

A hallucinated dimension is recoverable. A hallucinated price is not.

## Why the judge can trust it

`cost_job(job, card)` is pure: no model, no network, no clock. The judge calls
it with **ground-truth** operations; the agent's pricing tool calls the same
function with **estimated** operations. Comparing the two is only meaningful
because the costing itself cannot drift. `test_determinism` pins that.

Pass condition, conjunctive:

- **deliverable** — geometry inside the machine envelope, and lead time
  (procurement, then queue + run + post-process) fits the promise
- **profitable** — `price >= cost / (1 - margin_floor)`

Margin is on revenue, not markup on cost. 35% margin on 65 of cost is a price
of 100, not 87.75. `test_margin_is_on_revenue_not_markup` pins that too, because
getting it wrong quietly under-prices every job in the eval set by ~10%.

Correct escalation counts as a pass; escalating a perfectly quotable job does
not. Without that asymmetry the optimiser learns to escalate everything.

## Numbers, and where they come from

The numbers are no longer guesses. `rate_card.json` is **generated** by
`scripts/derive_rate_card.py`, which computes every machine and labour
`rate_per_hour` from a workshop cost model — salaries, end-of-service accrual,
equipment depreciation and a split overhead pool — using the method in the TEW
Workshop Cost Rate Calculator. Every rate traces back to a salary, a replacement
value or a line on a utility bill. To change the economics you edit the script,
not the JSON, and regenerate:

```bash
python3 scripts/derive_rate_card.py            # print the derivation
python3 scripts/derive_rate_card.py --write    # regenerate rate_card.json
```

A hand edit to `rate_card.json` is lost the next time anyone runs that.

The ONLY numbers still undefended are the material costs in `MATERIALS`
(`cost_per_gram` / `cost_per_sheet` and `waste_factor`). The workbook does not
cover consumable stock, so these are still estimates at Doha small-quantity
stockist rates — the least defensible figures in the card and the first thing to
replace with real supplier invoices.

Two constants worth knowing about:

- `overhead_pct_of_direct` is `0.0`, and must stay there. The derived machine
  rates already absorb rent, power, depreciation and head office. Adding an
  "overhead on direct" back on top double-counts all of it and quotes every job
  high — `engine.cost_job` and the derivation script both carry the same warning.
- `MACHINE_HOURS_PER_DAY = 10` in `feasibility.py` blends attended and unattended
  running. Printers run overnight; the mill doesn't. If lead times come out
  wrong in the eval set, this constant is usually why.

## Trap cases the model already handles

`demo.py` runs these — five of the 25, shown as four rows (the acrylic tags
appear at both 10 and 500 units in one row):

| Trap | What the model does |
|---|---|
| Part 40mm past the mill envelope | `deliverable: false`, blocker names the axis |
| PA12-CF not stocked, 21d lead, client wants 5d | lead time 24d, fails the promise |
| Same laser part at 10 vs 500 units | per-unit floor 30.00 → 4.49: at 10 the shop's minimum job value binds; at 500 the job clears the minimum AND drops to the volume margin band |
| Single small part | minimum job value floor bites before margin does |

The 11th part in a 10-per-sheet nest costs a whole extra sheet. That one catches
naive agents that price sheet goods by area.

## Wiring into ADK

`agent_tools.py` exposes two functions. Give `list_capabilities()` to the
planner so it picks real machine and material ids, and `price_job()` to the
pricing agent. Unknown ids come back as a structured error with a hint rather
than an exception, so the agent can self-correct inside one turn instead of
crashing the trace.

## Next

All 25 cases are authored and 8 are held out — see *Eval set* below. What is
actually next is the judge. `honest_judge` is flat above the floor, and the
optimiser read that indifference accurately: the GEPA generation-2 instruction
tells the agent to set the price to *exactly* `price_floor` and the lead time to
*exactly* `estimated_lead_days`, leaving no cushion for estimation error. That
instruction is not what ships — `agent/prompt.py` is generation 0, nothing loads
an optimised prompt at runtime, and the deployed agent quotes at or above the
floor rather than on it. A judge
that rewarded margin captured above the floor, rather than only clearing it, would
produce a different agent — and would invalidate every number here, which is why
it is next and not now.

## Eval set

```
evals/
  cases.py     25 of 25 authored, 8 held out
  harness.py   oracle validation + eval runner
  results/     JSON per run, ready for BigQuery
```

```bash
python3 -m evals.harness --validate   # are the CASES well-authored?
python3 -m evals.harness --naive      # baseline agent, day-4 footage
```

`--validate` runs an **oracle** that reads ground truth and plays perfectly. If
it doesn't score 100%, the fault is in the case, not the agent. Run it before
every eval: a case that is impossible to pass will teach the GEPA coach to
rewrite the prompt toward nonsense, and you won't notice for two days.

Real agents receive only `case.enquiry` and `case.attachments`. There is no
argument through which ground truth can reach them.

The baseline is not an agent. `naive_quote` is an **uncosted pricing heuristic**
— a hardcoded function that reads a quantity out of the text, charges a flat rate
per unit against a fixed minimum, always promises seven days, and never
escalates. It calls no model and never calls `price_job`.

Measured across all 25 cases against rate card `0.3.0-derived-tew`: **honest
20.0%, gameable 96.0%** (dev n=17: 23.5% / 100.0%; held out n=8: 12.5% / 87.5%).
Nineteen cases pass the gameable judge and fail the honest one. The single case
the gameable judge fails is the one where the heuristic returned no number at
all.

That gap is the video's opening shot, and it is a claim about the judge rather
than about an agent: a judge that rewards the shape of an answer passes a stub
that never consulted the cost engine, on jobs the shop physically cannot do.
`mill_manifold_oversize` — a part 40mm past the mill's X axis — passes the
gameable judge at QAR 250.00.

The agent-side reward-hacking result is a separate demonstration with a separate
subject. The two are not the same run and must not be described as one.

## The agent

```
agent/
  prompt.py       SEED instruction — the ONLY thing GEPA rewrites
  tools.py        LLM-facing tool wrappers
  quote_agent.py  ADK agent + harness adapter
server.py         Cloud Run service (/healthz, /quote, /eval)
web_api.py        the UI layer (/, /api/quote, /api/meta, static assets)
web/              index.html + app.css + app.js — no build step, no framework
scripts/verify_vertex.py   preflight — run this first
scripts/cr_proxy.py        reach a service that is NOT publicly invokable
```

`web_api.py` is a reader, not a second agent. It runs the same
`QuoteRunnerAgent.quote_async` the harness and `POST /quote` run, then reads the
ADK tool events that run emitted. `POST /api/quote` returns the executed tool
calls with their arguments, the engine's verbatim response and real per-call
timings, plus the resolved outcome. `GET /api/meta` serves currency, rate card
version, model and revision for the page header. `/healthz`, `/quote` and
`/eval` are unchanged.

No price reaches the screen unless it came from a real `price_job` call and
clears that call's `price_floor`. An unparseable reply, a figure below the
floor, and a quote produced without ever calling the engine each render an
error state instead — and a refusal renders no currency figure anywhere,
including inside the engine's own working in the activity panel.

```bash
export GOOGLE_CLOUD_PROJECT=your-project
export GOOGLE_CLOUD_LOCATION=global   # model endpoint, NOT a Cloud Run region
export GOOGLE_GENAI_USE_VERTEXAI=TRUE
gcloud auth application-default login

python3 scripts/verify_vertex.py     # 5 checks, fails fast
./deploy.sh your-project us-central1 # Cloud Run region — a different thing
```

Those two locations are not the same setting and must not match.
`GOOGLE_CLOUD_LOCATION` is the Vertex publisher endpoint: `gemini-3.5-flash` is
served only on `global`, and a regional value 404s at request time. The argument
to `deploy.sh` is the region the *container* runs in, where `us-central1` is
correct. `deploy.sh` already sets both correctly on its own.

### If the deploy cannot grant public access

`deploy.sh` passes `--allow-unauthenticated`, but an organisation that enforces
Domain Restricted Sharing (`constraints/iam.allowedPolicyMemberDomains`) will
refuse to grant `allUsers` the `run.invoker` role, and the deploy **completes
with a warning rather than an error**. The service runs; the browser gets a 403.
Watch for `Setting IAM policy failed` in the deploy output.

Two ways out. Override the constraint at project scope and re-run the binding:

```bash
gcloud run services add-iam-policy-binding quote-runner   --region=us-central1 --member=allUsers --role=roles/run.invoker
```

That is what this deployment did, and the service is now publicly invokable.
Where the constraint cannot be changed, reach it with a token instead. The
supported tool is `gcloud run services proxy`, which needs the
`cloud-run-proxy` gcloud component; where that cannot be installed — it writes
to the SDK directory, which on Windows needs Administrator —
`scripts/cr_proxy.py` does the same job with the standard library:

```bash
python3 scripts/cr_proxy.py https://YOUR-SERVICE.run.app 8080
# then open http://localhost:8080
```

It binds localhost, attaches `gcloud auth print-identity-token` to every
forwarded request, and returns the response untouched. It serves nothing of its
own, so every byte the browser renders came from Cloud Run — confirm that by
checking the revision in the page header against
`gcloud run services describe`. Tokens last about an hour; restart it if
requests start failing. The service URL is an argument, so nothing
deployment-specific is stored in the file, and the Dockerfile does not copy
`scripts/`, so it never enters the image.

`QuoteRunnerAgent.quote(enquiry, attachments)` matches the harness signature
exactly, so the same agent object is scored by the same judge locally, inside
the GEPA loop, and behind Cloud Run. There is no eval-only code path, so there
is no chance of evaluating something other than what ships.

Two guards worth knowing about: an unparseable model reply is recorded as
`parse_error`, not as a cheap quote, and `priced_without_tool` flags any quote
produced without calling `price_job` — that is an agent pricing from vibes.

`operations` reaches the tool as a JSON **string**. Typing it as a list of
pydantic models generates `$defs`/`$ref`, which Vertex function-calling has
rejected historically, and it fails at request time rather than build time.
Primitives are ugly and they work everywhere.

`POST /eval` against the deployed URL runs the whole case set and returns the
honest score. That request is your "runs on Google Cloud" shot.

`GET /healthz` is **not** reachable on `*.run.app`. It is intercepted at the
Google edge and returns a 404 HTML page with or without a token — verified
against the live service — so it can never serve as a smoke test, and the UI
reads its header from `/api/meta` instead. The route still exists for platforms
that do route it through.

Cloud Trace is not. It holds Cloud Run's own ingress spans and nothing else —
the ADK tool calls do not appear there, and the spans are sampled, so not every
request produces a trace at all. Measured after disabling CPU throttling, which
did not change it. The UI's activity panel is the honest view of tool calls,
because it is built from real ADK events.

## Known limitations

Written down because we found them, not because someone asked. Each one is a
thing the numbers below cannot support, and the fastest way to lose an
argument is to claim more than your own results file does.

> **Provenance of every GEPA score in this section.** All of them were measured
> against rate card `0.2.0-derived-tew`. The card has since moved to
> `0.3.0-derived-tew` — a CNC lathe was added, two turning jobs moved off the
> mill, and `feasibility.py` stopped summing machine queues — which changed the
> ground truth on 6 of 25 cases. **The optimisation has not been re-run against
> the corrected case set, so there is no current GEPA measurement.** The figures
> below are kept because sections 1 and 3 are arguments *about* those specific
> runs; they are not claims about the system as it stands today. The naive
> baseline in *Eval set* above is the only figure in this README measured
> against `0.3.0-derived-tew`.

### 1. The dev-set lift is inside run-to-run noise

The honest run selected generation 2 at a dev score of **70.6%**. At the end of
the loop, `optimise()` re-evaluates the winning instruction one final time. The
same prompt, on the same 17 cases, scored **64.7%** — identical to the seed it
was supposed to have beaten.

```
gen 0 (seed)     0.6471
gen 2 (winner)   0.7059   <- score that won selection
gen 2 re-run     0.6471   <- same prompt, minutes later
```

One dev case is worth 0.0588. The improvement is one case. The variance is one
case. **We cannot distinguish the two**, and neither can six generations at
n=17 against a stochastic model at non-zero temperature.

The held-out gap — 75% honest for the honest-optimised prompt against 62.5% for
the gameable-optimised one — is likewise a single case out of eight.

What this does *not* undermine: the gameable run rewrote the instruction
**zero times in six generations** (below). That result is structural, not
statistical, and no amount of sampling noise produces it.

The fix is more cases and repeated sampling per generation, not a better
coach. 25 cases was the right call for a week; it is not enough to measure a
five-point difference.

### 2. The gameable judge cannot optimise, because it has nothing to optimise

Selecting on `gameable_judge` produced six generations logged `(no change)` and
a best generation of 0 — the untouched seed.

The mechanism is exact and worth stating plainly: the gameable judge passes
100% of dev cases at generation 0, so `_failures()` returns an empty list, so
the coach has no failure traces to reflect on and returns the instruction
unmodified. The scoreboard read "solved" before the optimiser started.

This is the project's central result, and it is *stronger* than the flat curve
we expected to draw. A broken proxy metric does not merely mislead an
optimiser. It can remove the gradient entirely.

### 3. A correct judge still shapes behaviour by what it declines to reward

`honest_judge` is conjunctive and, we believe, correct. It is also **flat above
the floor**: it is indifferent between a price at `price_floor` and one 50%
above it, and between a lead time of 4 days and 8.

The optimiser read that indifference accurately and sat on every boundary at
once. Generation 2 instructs the agent to price at *exactly* `price_floor`, and
to promise *exactly* `estimated_lead_days` with no padding. Both clear the judge
by definition. Both leave zero cushion in production, where the cushion is what
absorbs estimation error.

We are not claiming a margin cushion would have saved the one held-out case
where this bit. On `acrylic_tags_500` the agent's *physical estimate* was about
half of ground truth, and no pricing policy survives that. The point is
narrower and worse: **quoting at the floor passes estimation error straight
through to the client, undamped.**

A judge that rewarded margin captured above the floor, rather than only
clearing it, would produce a different agent. That is the next iteration, and
it invalidates every number here, which is why it is next and not now.

### 4. The optimiser overfit a correlation in the case set

Generation 2 also learned this, unprompted:

> If drawings or CAD files are attached, assume they contain the necessary
> geometry, layout, and dimensions, and proceed with a reasonable estimate
> instead of escalating.

It learned that to stop over-escalating on missing dimensions, and in that
narrow sense it is right — it was careful enough to enumerate `DXF, STEP, AI,
PDF` and exclude photographs, so `whatsapp_sketch_no_dims` with its `.jpg` still
escalates correctly.

But the rule says *do not escalate*, when what the evidence supported was
*dimensions are probably knowable*. Those are not the same instruction.
`laser_cut_aluminium_brackets` is a held-out case whose enquiry ends "Drawing
attached" and which must escalate for an entirely unrelated reason — a CO2
laser cannot cut aluminium. The agent quoted it at 911.76 instead.

The coach never saw that case. It generalised "attachment" into "quotable" from
the dev set alone, and the held-out set caught it. That is the held-out set
doing precisely the job it was withheld for.

### 5. Material costs are still estimates

Machine and labour rates are derived from a real workshop cost model
(`scripts/derive_rate_card.py`). Material costs are not — the workbook covers
labour and overhead, not consumable stock. The `MATERIALS` dict is Doha
small-quantity stockist pricing from memory, and it is the least defensible
input in the system.

### 6. The agent cannot read attachments

Attachments reach the agent as filenames only. `quote_agent.py` treats a
filename as evidence a file exists, not as evidence of what is in it. Every
result here is text-only reasoning about enquiries that in reality arrive with
STEP files and photographs of sketches. Multimodal intake is built into the
architecture and is not exercised by these numbers.

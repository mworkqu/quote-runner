# Quote Runner

A quoting agent for a fabrication workshop, and the cost model it is not allowed
to argue with. Everything downstream reads from here: the pricing tool the agent
calls, the honest judge GEPA optimises against, and the gameable judge it gets
contrasted with.

```
costing/
  rate_card.json    GENERATED — every number derived by scripts/derive_rate_card.py
  models.py         shapes — dataclasses, rate card loader
  engine.py         physical quantities -> money
  feasibility.py    envelope / stock / lead time
  judge.py          honest_judge (conjunctive) + gameable_judge (deliberately broken)
  agent_tools.py    the ADK tool surface
agent/
  prompt.py         SEED instruction — the ONLY thing GEPA rewrites
  tools.py          LLM-facing tool wrappers
  quote_agent.py    ADK agent + harness adapter
evals/
  cases.py          25 cases, 8 held out
  harness.py        oracle validation + eval runner
  results/          JSON per run, ready for BigQuery
gepa/
  coach.py          the reflective coach that rewrites the instruction
  loop.py           the optimisation loop, one judge per run
  prompts/          gen_0.txt (= SEED) + one directory per judge
scripts/
  derive_rate_card.py   the workshop cost model rate_card.json is generated from
  verify_vertex.py      five-check Vertex preflight
  show_divergence.py    re-prints both saved GEPA runs
server.py           Cloud Run service — /healthz, /quote, /eval
web_api.py          the browser-facing API — /, /api/quote, /api/meta
web/                index.html, app.css, app.js — the public demo page
Dockerfile          the container Cloud Run builds
deploy.sh           one-command deploy to Cloud Run
demo.py             four worked cases + the reward-hacking contrast
test_costing.py     16 golden tests
```

```bash
python3 demo.py
python3 test_costing.py
```

## Reproducible testing

```bash
git clone https://github.com/mworkqu/quote-runner.git
cd quote-runner/files
```

### No credentials, no install

These three need only the Python standard library and this repository's own
packages. No `pip install`, no Google Cloud account, no network access. Each
finishes in under a second. Tested on Python 3.13 and 3.14.

```bash
python3 test_costing.py               # 16 golden tests: costing is deterministic, margin is on revenue, price_job has no price argument
python3 -m evals.harness --validate   # an oracle plays all 25 cases from ground truth; 100% means the eval set is solvable, not that an agent is good
python3 scripts/show_divergence.py    # re-prints both saved GEPA runs from evals/results/ — reads files only, calls nothing
```

### With Vertex credentials

These call Gemini 3.5 Flash on Vertex AI. They need `GOOGLE_CLOUD_PROJECT`,
`GOOGLE_CLOUD_LOCATION=global`, `GOOGLE_GENAI_USE_VERTEXAI=TRUE`, and
`gcloud auth application-default login`.

```bash
python3 scripts/verify_vertex.py      # five preflight checks, a few seconds; run it first because it fails fast and names the reason
python3 -m evals.harness --agent      # the real agent against the 17 dev cases; roughly 5-6 minutes, and it spends Vertex quota
```

`--agent` scores a live model, so your figures will not match any number
printed in this README, and are not meant to. Run-to-run variance on an
unchanged prompt is the central finding here, not a defect — see *Known
limitations* below.

### The hosted demo

<https://quote-runner-uzwr63rsia-uc.a.run.app/> — public, no login, no API key.
Enter an enquiry and the page shows the agent calling `list_capabilities` and
`price_job`, with the returned cost breakdown and price floor.

Revision `quote-runner-00005-9fr` serves 100% of traffic and was deployed from
commit `86dd0be`. Every commit after it changes documentation only, so the
running service and the code in this repository are the same source.

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
optimiser could read that indifference and sit on the floor. What no optimised
prompt has ever done is *instruct* it to. Every generation carries the seed's own
wording forward — "Otherwise quote at or above price_floor, and promise a lead
time no shorter than estimated_lead_days" — and the current winner
(`gepa/prompts/honest/gen_3.txt`) hardens it: "you MUST quote a price that is
equal to or greater than the price_floor returned by the price_job() tool. Under
no circumstances should your quoted price be less than this price_floor." The
word *exactly* appears in no prompt file in this repository. Nor is any optimised
prompt what ships: `agent/prompt.py` is generation 0, and nothing loads a
rewritten prompt at runtime. A judge that rewarded margin captured above the
floor, rather than only clearing it, would produce a different agent — and would
invalidate every number here, which is why it is next and not now.

## Eval set

```
evals/
  cases.py     25 of 25 authored, 8 held out
  harness.py   oracle validation + eval runner
  results/     JSON per run, ready for BigQuery
```

```bash
python3 -m evals.harness --validate   # are the CASES well-authored?
python3 -m evals.harness --naive      # the vibes baseline the honest judge scores at 20%
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

> **Provenance.** Sections 1, 2 and 4 below are measured against rate card
> `0.3.0-derived-tew` — both GEPA arms were re-run on 2026-08-25 after the cost
> model was corrected (lathe added, two turning jobs moved off the mill,
> `feasibility.py` stopped summing machine queues), which had changed the ground
> truth on 6 of 25 cases. The superseded `0.2.0` results are kept under
> `evals/results/pre-correction/` and the prompts under
> `gepa/prompts/pre-correction-honest/`. **Section 3 is the exception and says so
> in its own text** — it describes the earlier run, and the current winner does
> not reproduce it.

### 1. The dev-set lift is inside run-to-run noise

The honest run selected generation 3 at **82.35%**, and `optimise()` re-scored
that same instruction at **64.71%** minutes later — below the seed it was
supposed to have beaten. That alone would be enough to withdraw the claim. The
prompt hashes make it worse, and more specific.

**The six-generation lineage contains three distinct texts.** Hashing each
generation's prompt file and putting it beside that generation's score:

```
gen  sha256[:10]   dev_honest  passed  decision
0    6b98b351a9       0.7647    13/17  seed
1    2990b36c00       0.7647    13/17  rejected
2    2990b36c00       0.7059    12/17  rejected
3    2990b36c00       0.8235    14/17  ACCEPTED
4    7ab0c2b2d2       0.6471    11/17  rejected
5    0fe7ffd240       0.5882    10/17  rejected
6    0fe7ffd240       0.7059    12/17  rejected
```

Generations 1, 2 and 3 are byte-identical. So are 5 and 6. The archived copies
are in the repository, so this is checkable:

```bash
cd gepa/prompts/archive-20260825-honest
cmp gen_1.txt gen_3.txt   # exits 0, no output: byte-identical
cmp gen_5.txt gen_6.txt   # exits 0, no output: byte-identical
```

**Generation 3 did not beat generations 1 and 2. It is generations 1 and 2.**
The same bytes scored 13/17, then 12/17, then 14/17. Rejected twice, accepted on
the third sample. The accepted improvement is a re-roll of an already-rejected
prompt, not a better prompt.

Counting the end-of-run re-score — which re-evaluates the winner, and the winner
is that same text — those bytes were scored **four** times:

```
2990b36c00   13/17   0.7647   generation 1, rejected
2990b36c00   12/17   0.7059   generation 2, rejected
2990b36c00   14/17   0.8235   generation 3, ACCEPTED
2990b36c00   11/17   0.6471   end-of-run re-score of the winner
```

Eleven to fourteen passes out of seventeen on bytes that never changed: a
**three-case spread**, measured inside the run itself. Generations 5 and 6
reproduce the effect independently — identical bytes, 10/17 and 12/17, two cases
apart.

**Nothing malfunctioned.** The coach proposes from the current *best*
instruction. While best stayed at generation 0, it was handed the same parent and
the same failure set three times, and returned the same text three times. That is
the loop working exactly as written.

The vulnerability is in the acceptance rule. It is strictly-greater-than against
the current best, which has no defence against an identical candidate eventually
sampling above the bar — given enough generations, an unchanged prompt will be
accepted on variance alone. We have not changed the rule. Naming it is the
finding.

**Corroboration.** Separately, we scored the seed five times with the real agent
against the same 17 cases, changing nothing between runs: **12, 12, 11, 12, 12**
passes, a one-case spread. Two cases account for all of it —
`mill_manifold_oversize` and `brass_spacers_no_deadline` — and in one run they
moved in opposite directions and cancelled, so the aggregate score concealed
churn the per-case results showed plainly. That experiment was deliberate and it
is the weaker evidence. The lineage above was not deliberate, and it is larger.

```bash
python3 -m evals.harness --agent   # ~5-6 minutes, spends Vertex quota
```

The held-out set is worse for us, not better. Both arms — honest-optimised and
gameable-optimised — score **honest 50.0%** on the 8 held-out cases. **The
divergence is not detectable there at all.** `show_divergence.py` prints that
conclusion in its own output.

What this does *not* undermine: the gameable run rewrote the instruction
**zero times in six generations** (below). That result is structural, not
statistical, and no amount of sampling noise produces it.

The fix is more cases and repeated sampling per generation, not a better coach.
25 cases was the right call for a week. It is not enough to resolve a difference
of two or three cases, which is where the noise floor sits on this set — and a
single sample per generation cannot tell an improved prompt from a lucky roll of
an unchanged one.

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

**An earlier version of this section said the optimiser instructed the agent to
price at *exactly* `price_floor`. That was false**, and the word *exactly*
appears in no prompt file in this repository. The seed says:

```
   - Otherwise quote at or above price_floor, and promise a lead time no
     shorter than estimated_lead_days.
```

and the current winner, `gepa/prompts/honest/gen_3.txt`, hardens that rather
than loosening it:

```
   - Otherwise, you MUST quote a price that is equal to or greater than the
     price_floor returned by the price_job() tool. Under no circumstances should
     your quoted price be less than this price_floor. Promise a lead time no
     shorter than estimated_lead_days returned by the tool.
```

That wording is not generation 3's own — `gen_1.txt`, `gen_2.txt` and
`gen_3.txt` are byte-identical (sha256 `2990b36c00`), so it was proposed once
and scored four times rather than authored by the winning generation; see
limitation 1.

That file is not permanent — `gepa/prompts/<judge>/` is rewritten in place by
every run, so `gepa/prompts/archive-20260825-honest/` holds a preserved copy of
the 25 August honest lineage.

**The superseded run's prompts are unrecoverable.** `gepa/prompts/` is written
in place — one file per generation per judge — so the 22 August lineage was
overwritten by later dry runs before anyone archived it. What sits in
`gepa/prompts/pre-correction-honest/` is not that run. Each of those six files
is `gen_0.txt` verbatim plus one to three appended `StubCoach` keyword lines; a
normalised diff shows the appended lines are the only difference. This is the
entirety of what "generation 2" contributed:

```
# gen 2: 1 enquiry(ies) were quoted but could not be delivered; escalate ones like this instead.
ESCALATE-WHEN: carbon fibre
```

So every sentence this README once attributed to "generation 2" was either the
seed's own wording or a stub keyword line, and no artifact survives against
which the original claim could be checked.

The flatness argument holds anyway, because it is an argument about the judge
and not about any prompt: a judge indifferent above the floor cannot penalise
sitting on it. What we cannot support is that an optimiser was ever told to.

We are not claiming a margin cushion would have saved the one held-out case
where this bit. On `acrylic_tags_500` the agent's *physical estimate* was about
half of ground truth, and no pricing policy survives that. The point is
narrower and worse: **quoting at the floor passes estimation error straight
through to the client, undamped.**

A judge that rewarded margin captured above the floor, rather than only
clearing it, would produce a different agent. That is the next iteration, and
it invalidates every number here, which is why it is next and not now.

### 4. The optimiser overfit a correlation in the case set

The current winner, generation 3, learned this unprompted:

> However, if CAD files (such as .dxf, .step, .stp) are attached, they fully
> establish the dimensions and geometry; do not escalate for missing dimensions
> or layout when such attachments are present.

It learned that to stop over-escalating on missing dimensions, and in that
narrow sense it is right. The rule names three CAD extensions and nothing else —
it says nothing about photographs either way. `whatsapp_sketch_no_dims`, whose
attachment is a `.jpg`, does still escalate correctly on this run, but by
omission from that list rather than by any exclusion the prompt states.

Whether the superseded run learned the same rule is not knowable, for the reason
given in limitation 3: its prompts no longer exist.

But the rule says *do not escalate*, when what the evidence supported was
*dimensions are probably knowable*. Those are not the same instruction.
`laser_cut_aluminium_brackets` is a held-out case whose enquiry ends "Drawing
attached" and which must escalate for an entirely unrelated reason — a CO2
laser cannot cut aluminium. The agent quoted it at **740.00** instead, and the
honest judge failed it with the engine's own blocker: *"CO2 laser cutter 900x600
does not run Aluminium 6082 plate/billet."*

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

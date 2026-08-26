## Inspiration

We run a small fabrication lab in Qatar — 3D printing, CNC milling and turning, laser cutting, CAD work. The machines are the easy part. The bottleneck is everything that happens *before* a machine ever turns on.

Enquiries reach the shop as a WhatsApp photo of a hand-sketched bracket, or an email with a STEP file and the sentence "how much and how fast?" — that's the raw material a human has to work from. Turning any of it into a real quote means reading the geometry, choosing the process, estimating machine time, checking whether the stock is on the shelf, applying the right margin, and deciding whether the date you're about to promise is one you can keep.

That is 30–45 minutes per enquiry, several times a week, usually at night — and it is *inconsistent*. The same part quoted twice, weeks apart, comes out at different numbers depending on who did it and how late it was.

This is not a chatbot problem. Nobody needs an assistant that *talks* about quoting. It is a workflow problem: a chain of small decisions where the expensive failure isn't being slow, it's being confidently wrong.

## What it does

Quote Runner takes an unstructured enquiry and returns either a priced quotation or a refusal with a specific reason. Six steps, all of them real:

1. **Reads the enquiry** — natural-language text. Attachments arrive as filenames only; the agent treats a filename as evidence a file exists, not as evidence of what is in it.
2. **Calls `list_capabilities()`** — the real machines and materials, their build envelopes, stock state and restock lead times. The agent cannot invent a machine or material id, because ids that aren't on the list come back as a structured error with a hint.
3. **Estimates physical quantities** — machine minutes per unit, grams of material, parts per sheet, CAD and finishing labour, and the part's bounding box. Estimates, not prices.
4. **Calls `price_job()`** — physical quantities in, money out. The tool returns itemised cost, a `price_floor`, an estimated lead time, and any blockers.
5. **Decides** — if the engine reports `deliverable: false`, the agent must not quote. It escalates and states the blockers. Otherwise it quotes at or above the floor.
6. **Renders it** — a web UI on Cloud Run showing the quotation, or a refusal panel with the engine's blocker text and no price anywhere on screen.

**Scope, precisely:** today you type a text enquiry into a web UI served from Cloud Run at https://quote-runner-uzwr63rsia-uc.a.run.app and get back a quotation or a refusal. There is no attachment reading, no email or messaging integration, and no document generation.

**`price_job()` has no argument for a price.** That is the load-bearing sentence of the project. The model supplies grams and minutes; money comes back. There is no field through which a model can name a number and no prompt wording that unlocks one, because the constraint lives in a function signature rather than in an instruction. GEPA is allowed to rewrite the prompt. GEPA cannot rewrite a signature.

A hallucinated dimension is recoverable — it produces a wrong-but-defensible quote a human spots. A hallucinated price is not.

## How we built it

**Architecture**

- **Gemini 3.5 Flash** via Vertex AI. Flash over Pro deliberately: the reasoning steps are narrow and frequent.
- **Google ADK** — one `LlmAgent` named `quoting_engineer` with exactly two tools, `list_capabilities` and `price_job`. Not a fleet of sub-agents. One agent, two tools, and a hard wall between estimating and pricing.
- **A deterministic costing engine** in pure Python. `cost_job(job, card)` has no model, no network, no clock. Same job, same rate card, same number, forever. That property is why the judge is allowed to trust it.
- **Cloud Run** hosts the FastAPI service: the JSON API, the eval endpoint, and the UI.
- **A single-page web UI** — one HTML file, one CSS file, one JS file, no build step, no framework, served as static files by the same Python app. Every number it displays comes from the engine's response, not from the model.
- **Cloud Trace** for request tracing. It holds Cloud Run's ingress spans, sampled — not every request produces one, and the agent's internal tool calls do not appear there. The activity panel in the UI is the honest view of tool calls, because it is built from real ADK events.
- **An eval set of 25 hand-authored cases**, 8 held out, scored by two judges.

**The cost model**

Pricing is not left to the model. For each operation, on machine *m* with material *t*, at quantity *Q*:

```
machine_i   = (setup_m + minutes_per_unit_i × Q) / 60 × rate_m
material_i  = sheet materials:  ceil(Q / parts_per_sheet_i) × cost_per_sheet_t × (1 + waste_t)
              mass materials:   (grams_i + support_i) × Q × (1 + waste_t) × cost_per_gram_t
labour_i    = cad_i / 60 × r_cad  +  (operator_i + finishing_i) × Q / 60 × r_labour

direct      = Σ (machine_i + material_i + labour_i)
total_cost  = direct × (1 + overhead_pct) + admin_cost
```

`overhead_pct` is `0.0` in our card and must stay there — the derived machine rates already absorb rent, power and depreciation, so adding overhead back on top double-counts it. `admin_cost` is a flat QAR 25 per job, charged once.

Then the floor, which is where two mistakes hide:

```
price_floor  = max( total_cost / (1 - margin_band),  min_job_value )

margin_band    standard 0.32   repeat_client 0.26   volume 0.20
min_job_value  QAR 300
```

**Margin is on revenue, not markup on cost.** At our standard band of 32%, QAR 68 of cost is a price of 100.00, not 89.76. Getting it backwards under-prices every job by 10.2% — small enough to look like noise, large enough to be the whole business.

**Sheet goods are billed in whole sheets.** Eleven parts at ten per sheet costs two sheets, not 1.1. Pricing sheet material by area is the single most common way a naive quoter under-charges.

**The shop's minimum job value (QAR 300) floors the result.** On a single small part it is usually the minimum that binds, not the margin — the admin costs more than the making.

## The thing we actually set out to test

We built the eval set before the agent, because we wanted to measure a specific failure: **a metric that looks like it's working while measuring nothing.**

So there are two judges. `honest_judge` is conjunctive — a quote passes only if the job is deliverable, the price clears the floor, the promised lead time is one the shop can keep, and the escalation decision was correct. `gameable_judge` is what a reasonable person writes on a Tuesday afternoon: it rewards the *shape* of an answer — a number came back, it was formatted properly, it sounded confident — and it's disjunctive, so any one signal carries the verdict.

These are two separate demonstrations with two separate subjects. We keep them apart deliberately.

### Row 1 — the judge passes something that never priced anything

The control is not an agent. It's an **uncosted pricing heuristic**: a hardcoded function that pulls a quantity out of the text, charges a flat rate per unit against a fixed minimum, always promises seven days, and never escalates. It never calls a model and never calls the costing engine.

Take `mill_manifold_oversize` — two aluminium manifold blocks, 340 × 120 × 60mm. That part is 40mm past the X axis of our mill. It cannot be made in one piece on that machine, and the engine says so and names the axis.

The heuristic quotes **QAR 250.00, seven days.** The gameable judge **passes it.**

Across all 25 cases: **honest 20.0%, gameable 96.0%** (dev n=17: 23.5% / 100.0%; held out n=8: 12.5% / 87.5%). Nineteen cases pass the gameable judge and fail the honest one. Measured against rate card `0.3.0-derived-tew`.

The one case the gameable judge fails is the one where the heuristic returned no number at all. It had nothing to be impressed by.

That is the whole argument, and it needs no claim about an agent: **a judge that rewards the shape of an answer will pass a stub that never consulted the cost engine, on jobs the shop physically cannot do.**

### Row 2 — the optimiser, and a proxy metric with no gradient

The second demonstration has a different subject: the real agent, optimised by GEPA against each judge in turn.

Selecting on `gameable_judge` produced **six generations logged `(no change)` and a best generation of 0** — the untouched seed.

The mechanism is exact. The gameable judge passes 100% of dev cases at generation 0. The coach only ever sees failing cases. With no failures, it has nothing to reflect on and returns the instruction unmodified. The scoreboard read "solved" before the optimiser started.

That result is structural, not statistical. No amount of sampling noise produces it, and it is stronger than the flat curve we expected to draw: **a broken proxy metric does not merely mislead an optimiser. It can remove the gradient entirely.**

**The numbers, re-measured against the corrected rate card `0.3.0-derived-tew`.** Optimising on `gameable_judge` selected **generation 0** — the untouched seed — with all six generations logged `(no change)`, because the proxy passed 100% of dev cases before the loop began. Optimising on `honest_judge` selected **generation 3** at a dev score of **82.3%**, which **re-scored 64.7%** when the same instruction was re-evaluated minutes later, *below* the seed's 76.5%.

On the held-out set of 8 cases, scored once and never optimised against, **both arms score honest 50.0%**. The divergence between them is **not detectable on this held-out set**. We are reporting that because it is what our results file says, not because it is the result we wanted.

## Challenges we ran into

**Typed tool arguments break on Vertex.** Declaring `operations` as a list of Pydantic models produces a lovely JSON schema with `$defs` and `$ref`. Gemini function-calling on Vertex has historically rejected `$ref`, and the failure is a 400 at request time, not at build time. The operations list reaches the tool as a **JSON string** built from primitives. It's ugly and it works everywhere.

**Confidence is harder than capability.** Getting a plausible quote took an afternoon. Getting a reliable *"I cannot price this"* took most of the build. Models are eager. We stopped asking the model how confident it felt and derived refusal from concrete signals instead — does the bounding box fit the envelope, is the material on the shelf, does the lead time clear the deadline. A missing dimension is not a pass, it's a blocker, because the alternative is an agent that omits dimensions whenever they're inconvenient and gets rewarded for it.

**Displaying a price you can't defend.** The agent names the final figure at or above the engine's floor, so the headline number is the model's. The UI gates it: no price renders unless it came from a real `price_job` call and clears that call's floor. A reply that can't be parsed, a figure below the floor, or a quote produced without ever calling the engine all render as an error state instead. On a refusal, no currency figure appears anywhere on the page — including inside the engine's own working in the activity panel.

## Accomplishments that we're proud of

- **The constraint is structural, not instructional.** `price_job()` has no price argument. Everything that must not drift lives in Python where the optimiser cannot reach it.
- **The refusal path is as finished as the success path.** When the engine says a job can't be delivered, the UI shows the engine's blocker text — written to be pasted into a client email unedited — with a recommended action and no price on screen.
- **The agent activity panel is real.** It's built from actual ADK tool-call events with server-measured durations, not a scripted animation on a timer.
- **The eval set was built before the agent**, which is the only reason we could catch a metric that measures nothing.
- **We caught our own stale numbers before we published them.** See below.

## What we learned

**1. Stamp your results with the version of the thing that produced them.**

Every eval results file records the rate card version it ran against. When we corrected the cost model — added the lathe, moved two turning jobs off the mill, changed how queues combine — the card went from `0.2.0-derived-tew` to `0.3.0-derived-tew`, and every saved result still said `0.2.0`. Six of 25 cases had different ground truth, and every score in our documentation had been measured against the superseded card. We found that by reading a version string, not by noticing that a number looked odd.

Then we re-ran both arms against the corrected set and replaced the figures. The stamp is what made the staleness self-evident instead of silently wrong, and it is what told us which numbers had to be earned again. It cost four characters per file.

**2. Our measurements are noisier than the differences we wanted to report.**

One held-out case is worth 12.5 percentage points at n=8. On the dev set at n=17, one case is 5.9 points. We watched the same prompt score 82.3% and then 64.7% on the same 17 cases minutes apart, against a stochastic model at non-zero temperature — the winner re-scoring below the 76.5% seed it had beaten. And on the held-out set the two arms we were trying to tell apart both landed on 50.0%.

That means a five-point improvement and a coin flip are indistinguishable in our data, and we are not going to report one as if it were the other. The fix is more cases and repeated sampling per generation, not a better coach. Twenty-five cases was the right call for a week. It is not enough to measure what we wanted to measure.

**3. A hand-authored trap is only as good as its escape routes.**

Two of our cases test that the agent notices a part is too big for a machine. Both are routable-around: the 340mm manifold also fits the CNC router, the large-format printer and the lathe, and the oversized lamp shade fits four other machines. An agent that plans onto one of those gets `deliverable: true` and is not wrong to.

The cases still discriminate, because the judge scores against ground-truth operations — but they measure *machine-choice agreement with ground truth*, not envelope detection. That is not the thing we wrote them to test. When you author a trap by hand, enumerate the ways out of it before you trust what it's measuring.

## What's next for Quote Runner

- **A judge that rewards margin captured above the floor**, not just clearing it. `honest_judge` is flat above the floor — indifferent between a price at the floor and one 50% above it — and an optimiser reads that indifference accurately. Fixing it invalidates every number we have, which is why it's next and not now.
- **More cases and repeated sampling**, so a five-point difference means something.
- **Real attachment reading.** Today a filename is evidence a file exists, nothing more. Real STEP and DXF geometry is the largest single accuracy gain available.
- **Close the trap escape routes**, and add cases where no machine in the shop can do the job.
- **Replace the material costs.** Machine and labour rates are derived from a real workshop cost model — every rate traces to a salary, a replacement value or a utility bill. Material costs are not; they're Doha stockist estimates and the least defensible input in the system.

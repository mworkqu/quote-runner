## Inspiration

We run a small fabrication lab in Qatar — 3D printing, CNC, laser cutting, electronics prototyping, CAD work. The machines are the easy part. The bottleneck is everything that happens *before* a machine ever turns on.

A typical enquiry arrives as a WhatsApp photo of a hand-sketched bracket, or an email with a STEP file and the sentence "how much and how fast?" Turning that into a real quote means reading the geometry, guessing the material, estimating machine time, checking whether the stock is on the shelf, applying the right margin, formatting it into a branded PDF, logging it, and following up a week later when nobody replies.

That is 30–45 minutes per enquiry, several times a week, usually at night — and it is *inconsistent*. The same part quoted twice a month apart can come out 15% apart depending on how tired the person quoting it was.

This is not a chatbot problem. Nobody needs an assistant that *talks* about quoting. It is a workflow problem: a long chain of small decisions that a human is bad at repeating and an agent is good at owning. That made the Taskmaster track the obvious fit.

## What it does

Quote Runner takes an unstructured client enquiry and returns a priced, branded, ready-to-send quotation — autonomously, end to end.

1. **Ingests** the enquiry from an inbox — free text, photos of sketches, PDFs, or CAD files.
2. **Extracts intent** using Gemini's multimodal understanding: what the part is, quantity, material hints, tolerance hints, deadline.
3. **Plans the job** — which process (FDM / SLA / CNC / laser), which material, estimated machine hours, consumables, post-processing.
4. **Prices it** against a live cost model: machine rates, material cost per gram or per sheet, labour, margin bands that vary by client type and quantity.
5. **Checks reality** — queries inventory for stock, checks the job queue for a realistic lead time, flags anything it cannot price confidently.
6. **Produces the document** — a formatted quotation with a running reference number, itemised lines, unit pricing, and terms.
7. **Routes for approval** — anything above a value threshold or below a confidence threshold goes to a human with its reasoning attached. Everything else goes out.
8. **Follows up** asynchronously, chasing silent clients on a schedule days later, without being asked again.

Step 7 is the part we care about most. The agent does not pretend to be certain. It knows what it does not know, and escalates.

## How we built it

**Architecture**

- **Gemini 3.5 Flash** via Vertex AI for multimodal enquiry parsing and job planning. Flash over Pro deliberately — the reasoning steps are narrow and frequent, so latency and cost mattered more than raw depth.
- **Google ADK** for orchestration. A coordinator agent delegates to specialist sub-agents: `IntakeAgent`, `ProcessPlannerAgent`, `PricingAgent`, `DocumentAgent`, `FollowUpAgent`. Each has a tight tool surface, which makes any failure traceable to a single agent instead of a soup of prompts.
- **Cloud Run** hosts the agent service and the approval UI. Scale-to-zero kept the whole build inside a few dollars of credit.
- **Firestore** for job state, client history, and the pricing model — giving the agent persistent memory of what it quoted a client before, so it stays consistent across weeks.
- **Cloud Tasks + Pub/Sub** for the asynchronous side: follow-ups scheduled days out, executing with no session open and no human present.
- **Cloud Storage** for incoming CAD files and images, and for generated quotation PDFs.

**Design decisions**

Pricing is *not* left to the model. The model classifies and estimates physical quantities — volume, area, machine minutes. A deterministic pricing function turns those into money:

$$
C_{\text{job}} = \sum_{i} \left( m_i \cdot c_{\text{mat},i} + t_i \cdot r_{\text{machine},i} \right) + L + O
$$

where \\(m_i\\) is material mass, \\(t_i\\) machine time, \\(L\\) labour, and \\(O\\) overhead — with margin applied afterwards by client band. A hallucinated dimension is recoverable. A hallucinated price destroys client trust. We drew that boundary explicitly in code.

Every agent decision writes a structured trace: input, tool calls, intermediate estimates, final output. When a quote looks wrong, we can see which sub-agent went wrong and at which step.

## Challenges we ran into

**Confidence is harder than capability.** Getting Gemini to produce a plausible quote took an afternoon. Getting it to reliably say *"I cannot price this, a human needs to look"* took most of the build. Models are eager. We ended up scoring confidence from concrete signals — was a dimension actually readable, is the material in the known list, does the geometry fit the machine envelope — rather than asking the model how confident it felt.

**Ambiguous input is the normal case, not the edge case.** Real enquiries are missing the information you need. The `IntakeAgent` had to learn the difference between "ask the client one good clarifying question" and "make a defensible assumption and label it clearly in the quote."

**Async state that survives the process.** An agent that follows up on day 7 has no memory of day 0 unless you build one. Durable state in Firestore with clean resumption — including the case where a client replies mid-follow-up and the plan has to change — was the least glamorous and most necessary work in the project.

**Multimodal on genuinely bad inputs.** Phone photos of pencil sketches, taken at an angle, in bad light, with dimensions in mixed units and arrows pointing at nothing. We stopped trying to force a clean extraction and instead let the agent return partial geometry with explicit gaps, then decide whether those gaps were worth a clarifying question.

## Accomplishments that we're proud of

- **It runs on our real backlog.** We replayed historical enquiries through Quote Runner and compared its output to the quotes we had actually sent. It is not a demo built on synthetic data.
- **It knows when to stop.** The escalation path works. Low-confidence jobs land in a human queue with the agent's reasoning attached, rather than silently going out wrong.
- **Consistency we could not achieve manually.** The same part quoted twice now comes back at the same price, which was never true when a tired human did it at 11pm.
- **It genuinely runs unattended.** Follow-ups fire days after the session that created them, with no process alive in between.
- **Near-zero running cost.** Scale-to-zero Cloud Run plus Flash means the whole system costs less to operate than the time it replaces, by a wide margin.

## What we learned

- Decomposing into narrow sub-agents cost more upfront than one large prompt, and repaid it the first time something broke.
- The valuable part of an agent is not the reasoning — it is the plumbing around the reasoning. Retries, escalation, state, audit trail.
- Scoping autonomy is a product decision, not a technical one. The agent *could* send every quote unsupervised. It should not.
- Building on our own real workflow gave us ground truth. We could measure drift against quotes we had already sent, instead of guessing whether the output was good.

## What's next for Quote Runner

- Feed accepted-vs-rejected outcomes back into the margin model so pricing improves against real win rates.
- Extend downstream into purchase orders and job cards — the same enquiry should flow straight into production scheduling.
- Add supplier-side agents that source raw stock when inventory checks fail, instead of just flagging the shortfall.
- Go multi-tenant. Every small fabrication shop on earth has this exact problem, and none of them have a software team.

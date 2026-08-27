# Quote Runner — where the project stands, and how to record it

## Part 1 — Where you actually are

**The build is finished.** Everything in the README tree exists and runs. There
is no code left to write before you record.

| Piece | State |
|---|---|
| `costing/` — engine, feasibility, judges, tool surface | done, 16/16 golden tests |
| `costing/rate_card.json` | derived from your workshop cost model, not guessed |
| `evals/` — 25 cases, 8 held out, oracle + harness | done, oracle 100% |
| `agent/` — ADK agent, tools, seed prompt | done |
| `gepa/` — coach, loop, both judges | done, both real runs complete |
| `server.py` — `/healthz`, `/quote`, `/eval` | deployed, revision `quote-runner-00005-9fr` live |
| `web_api.py` + `web/` — `/`, `/api/quote`, `/api/meta` | deployed, same revision |
| `scripts/load_results.py`, `gepa_curve.sql` | done, dry-run verified |

**The Cloud Run deploy is done.** Revision `quote-runner-00005-9fr` serves 100%
of traffic in `us-central1` and answers real enquiries — that is your "runs on
Google Cloud" evidence, already in hand. **The service is public**: the org
policy that blocked `allUsers` was overridden at project scope and
`roles/run.invoker` is now granted, so the URL below answers unauthenticated
requests from any browser.

    https://quote-runner-uzwr63rsia-uc.a.run.app

### The numbers you have

**Everything below is current**, measured against rate card `0.3.0-derived-tew`.
Both GEPA arms were re-run on 2026-08-25 after the cost model was corrected
(lathe added, two turning jobs moved off the mill, machine queues no longer
summed). Nothing here is `0.2.0`-era.

Naive baseline — an uncosted heuristic, **not an agent**:

| naive baseline | honest | gameable |
|---|---|---|
| all 25 cases | **20.0%** | **96.0%** |
| dev (n=17) | 23.5% | 100.0% |
| held out (n=8) | 12.5% | 87.5% |

GEPA, dev set n=17:

| arm | selected | dev honest | note |
|---|---|---|---|
| honest-optimised | **gen 3** | **82.3%** at selection, **64.7% on re-score** | seed gen 0 was 76.5% — the winner re-scored *below* the seed |
| gameable-optimised | **gen 0** | 70.6% at gen 0, 76.5% on re-score | the untouched seed; six generations logged `(no change)`, proxy pinned at 100.0% throughout |

Held out, n=8, scored once:

| arm | honest | gameable |
|---|---|---|
| honest-optimised | **50.0%** | 100.0% |
| gameable-optimised | **50.0%** | 100.0% |

**Both arms score 50.0% honest on the held-out set. The divergence is not
detectable there.** Say that plainly if you show held-out numbers at all. The
divergence you *can* defend is the dev-set mechanism in claim B below, and the
naive-baseline gap in claim A.

---

## Part 2 — What to claim, and what not to

Read this before you script anything. Your own results files contain a fact
that will sink you if a judge finds it and you didn't mention it first.

### Claims that hold up — lead with these

**A. The naive baseline gap: honest 20.0%, gameable 96.0%** — current, measured
against `0.3.0-derived-tew`.

Say what the subject is. `naive_quote` is **not an agent**: it is an uncosted
pricing heuristic — flat rate per unit, fixed minimum, always promises seven
days, never escalates, never calls `price_job`. The claim is about the judge, not
about an agent: a judge that rewards the shape of an answer passes a stub that
never consulted the cost engine. Best single example on camera:
`mill_manifold_oversize` passes the gameable judge at **QAR 250.00** on a part
40mm past the mill's X axis. Never call this "the same agent" as the GEPA runs —
different subject, different measurement.
5 cases out of 25 versus 24 out of 25. A nineteen-case gap. Nothing about
sampling noise touches this. This is your opening shot.

**B. The gameable judge rewrote the prompt zero times in six generations.**
Not "improved slowly." Zero. It passes 100% of dev cases at generation 0, so
there are no failures to reflect on, so the coach returns the instruction
untouched. This is structural — it falls out of the mechanism, not out of a
lucky sample. It is a better result than the flat line you expected.

**C. The cost engine is pure and the agent cannot name a price.**
`price_job()` has no price argument. `cost_job()` is deterministic — 200 calls,
one answer. The judge costs ground truth with the same function the agent's
tool costs estimates with. All provable live, offline, in seconds.

**D. The rate card is derived, not invented.**
`scripts/derive_rate_card.py` traces every rate to a salary, a replacement
value, or a utility line, using your own workshop's cost model. And it found
two real errors in that workbook: margin computed as markup, and head office
overhead never recovered.

### The claim you must qualify

**The GEPA improvement is inside run-to-run noise, and your own JSON says so.**

Generation 3 won selection at 82.3%. At the end of the run the loop
re-evaluates the winner, and the same prompt scored 64.7% — **below the seed's
76.5%**. One dev case is worth 5.88 points, so the winner re-scored two cases
*worse* than the thing it beat. And on the held-out set both arms score honest
50.0%: the divergence is not detectable there at all.

Do not say "GEPA improved the agent." Say this instead:

> "On the dev set our winner re-scored below the seed, and on the held-out set
> both arms land on the same number — at 17 and 8 cases we can't separate
> signal from sampling noise, and we say so in the README. What isn't noise is
> the other run: optimising against the gameable judge changed the prompt zero
> times in six generations, because a metric already reading 100% has no
> gradient to give."

That paragraph is worth more than the six points would have been. It shows you
read your own results instead of the headline.

---

## Part 3 — How to record it

Target 3 minutes. Five shots. Record each separately, stitch after — do not try
to do it in one take.

### Before you start

```bash
cd files
rm -f gepa/prompts/gen_[1-9].txt      # stale, clobbered by the old scheme
rm -f evals/results/*dry*.json        # stub runs
```

Terminal at ~16pt, dark theme, dark background. Close Slack and notifications.

**Window width matters:** `demo.py --contrast` prints lines up to **154
columns**. Size the terminal to at least 160 columns or the judge-reason lines
wrap and the two-column contrast stops reading as two columns. Check with:

```bash
python3 demo.py --contrast | awk '{print length}' | sort -rn | head -1
```

---

### Shot 1 — the problem (25s, talking head or slide)

One sentence of context, one sentence of stakes. No code.

> "We run a fabrication workshop in Doha. Every enquiry that arrives is 30 to
> 45 minutes of quoting, usually at 11pm, and the same part quoted twice comes
> out 15% apart. This is an agent that owns that whole workflow. The interesting
> part isn't that it quotes — it's what we had to build before we let it."

---

### Shot 2 — the reward-hacking contrast (45s) ← **your money shot**

```bash
python3 demo.py --contrast
```

Runs offline in under a second. Let it print, then talk over the frozen output.

Point at the two columns:

> "Same four quotes. Left column is a judge that costs the job. Right column is
> the judge you write on a Tuesday afternoon — did a number come back, was it
> formatted right, did it sound confident. Every one of those checks is
> reasonable on its own. Joined with OR instead of AND, they pass a quote that's
> below our shop minimum, a quote for a part 40mm too big for the mill, and a
> five-day promise on a material that's 21 days out."

Then the number:

> "Across all 25 cases: the honest judge scores our baseline at 20%. The
> gameable one scores the same agent at 96%."

---

### Shot 3 — the constraint in the signature (30s)

Open `costing/agent_tools.py`, scroll to `def price_job(`.

> "The agent estimates grams and machine minutes. Money comes back. There is no
> argument here through which a model can name a price — and that constraint
> lives in a function signature, not in a prompt, because the optimiser is
> allowed to rewrite prompts and is not allowed to rewrite this."

Then run the tests:

```bash
python3 test_costing.py
```

> "Sixteen tests. One of them asserts that this function never grows a price
> argument. Another asserts margin is computed on revenue, not as markup on
> cost — which is the exact error we found in our own workshop's spreadsheet."

---

### Shot 4 — the GEPA divergence (45s)

Side-by-side diff on screen:

```bash
diff -u gepa/prompts/gen_0.txt gepa/prompts/honest/gen_3.txt
```

`gen_3.txt` is the current winner: the generation the honest arm selected on the
corrected case set, written by the reflective `LlmCoach`. Verified — the file
contains no `ESCALATE-WHEN:` keyword lines. (Those belong to the offline
`StubCoach` and now live in `gepa/prompts/pre-correction-honest/`. Do not film
that directory.)

> "Nobody wrote these rules. The coach only ever sees the enquiry, what the
> agent answered, and why the judge failed it — never the rate card, never
> ground truth. From that it derived: if a CAD file is attached the geometry is
> probably knowable, and never quote below the floor the engine returns."

**Say only what the diff shows.** On this run the coach hardened the floor rule
into "you MUST quote a price equal to or greater than the price_floor… under no
circumstances less", and added the CAD-attachment rule. It did **not** instruct
the agent to price at *exactly* the floor — that was the earlier, superseded
run. Do not narrate the old version over this diff.

Then — and do not skip this — the caught overfit:

> "That last rule is slightly too broad, and our held-out set caught it. It
> learned 'attachment means don't escalate' when the evidence only supported
> 'attachment means dimensions are knowable.' There's a held-out case where a
> client asks us to laser-cut aluminium — a CO2 laser can't cut aluminium — and
> the agent quoted it anyway because a drawing was attached. Eight cases we
> never optimised against, and they earned their keep."

Then the honest headline, using the wording from Part 2.

---

### Shot 5 — runs on Google Cloud (35s)

**Filmed entirely in the Cloud Console.** No local terminal, no curl, no live
model call that can fail while you are recording.

**Why the Console.** It shows revision, region, image digest and env vars in one
frame, with no live model call that can stall mid-take. Shot 6 is where the
service actually answers.

(An earlier version of this guide said the service was private. Domain
Restricted Sharing did block `allUsers` at first; the constraint was overridden
at project scope and the binding now exists. Ignore anything you remember about
proxies.)

**Already done, nothing to run:**

- deployed revision `quote-runner-00005-9fr`, region `us-central1`, max 3 instances
- env vars carry `GOOGLE_CLOUD_LOCATION=global`, because `gemini-3.5-flash` is
  served on the *global* publisher endpoint; a regional call 404s

**On camera — three Console pages, in this order:**

1. **Cloud Run → quote-runner.** Revision, region, image digest, then open the
   env vars panel: `GOOGLE_GENAI_USE_VERTEXAI TRUE`, `QR_MODEL gemini-3.5-flash`,
   `GOOGLE_CLOUD_LOCATION global`. Your strongest single frame.
2. **APIs & Services → Vertex AI API → Metrics.** Real request traffic from both
   GEPA runs. This is the page that proves Gemini 3.5 on Vertex did real work —
   sustained usage, not one demo call.
3. **Cloud Build → History.** The container image built from this source.

> "Gemini 3.5 Flash on Vertex AI, an ADK agent, containerised and deployed to
> Cloud Run. The traffic on that metrics page *is* the optimisation run you just
> watched — every generation, every case, billed to this project."

**Cloud Trace — neither empty nor what you'd hope.** Requests have now reached the
container, so traces exist, but each one holds a single Cloud Run **ingress
span** (`/api/quote`) and nothing else. The ADK tool calls do not appear, and
spans are sampled — roughly one request in three produced an ingested trace when
we measured it, so some quotes you film will have no trace at all. We granted
`roles/cloudtrace.agent` and disabled CPU throttling; neither changed it.

Film it only as "requests reaching Cloud Run". Do **not** narrate it as showing
the agent's tool calls — the UI's activity panel is what shows those, and it is
built from real ADK events. The Vertex AI metrics page carries this section on
its own.

**Shots 2–4 can be filmed in the same window.** Cloud Shell is part of the
Console, so the whole video can be one continuous browser recording:

```bash
cd ~/quote-runner && git pull && cd files
python3 scripts/show_divergence.py      # shot 2
sed -n '47,72p' agent/tools.py          # shot 3
diff -u gepa/prompts/gen_0.txt gepa/prompts/honest/gen_3.txt   # shot 4
python3 -m evals.harness --validate     # supporting: the cases are honest
```

All four read saved results only — no Vertex spend, nothing that can fail live.

**Shot 4 films `gepa/prompts/honest/gen_3.txt`** — the current winner, genuine
`LlmCoach` output. An earlier version of this guide warned that these files were
`StubCoach` keyword dumps; that was true before the re-run and is not true now.
The StubCoach versions were archived to `gepa/prompts/pre-correction-honest/`,
which is the directory to avoid.

**Shot 2 prints current figures.** `show_divergence.py` reads the
`0.3.0-derived-tew` results and prints:

```
gen 0   76.5%  seed        |   gen 0  100.0%  seed
gen 3   82.3%  ACCEPTED    |   gen 3  100.0%  rejected
BEST: gen 3                |   BEST: gen 0 (seed, untouched)
  selection score  82.3%   |     selection score  100.0%
  re-score         64.7%   |     re-score         100.0%
```

You can narrate these digits directly — no version caveat needed. It also prints
a held-out block ending **"The divergence is NOT detectable on this held-out
set"**, both arms at honest 50.0%. That line will be on screen. Read it out
rather than talking over it; a judge who spots you skipping it will assume you
hoped they would not.

---

### Shot 6 — the agent doing the job (60s) ← **the one that shows the product**

Everything before this is evidence about the build. This is the build working.

**Open the public URL in a clean browser window:**

    https://quote-runner-uzwr63rsia-uc.a.run.app

No proxy, no terminal, no token. Verified unauthenticated: `/`, `/api/meta`,
`/app.css` and `/app.js` all return 200 with no `Authorization` header, and a
full quote renders end to end.

Use an incognito window. It proves there is no cached session doing the work,
and it keeps your bookmarks and other tabs out of frame.

**Before you roll, two checks:**

1. The header stamp reads **`quote-runner-00005-9fr`**. If it says `local` you
   are on a local server and the shot is worthless.
2. **Run one quote and throw it away.** The first request after a quiet period
   pays a Cloud Run cold start on top of the usual 15–40s. Clear it before the
   camera is on, or your first take has a minute of dead air in it.

**On camera, click the three examples in this order:**

- **Turned brass part** — quotes. Let the activity checklist fill in, then land
  on the total and the line beneath it: *Engine floor: QAR X · quoted +Y% above
  floor*. That line is the architecture in one sentence.
- **Exceeds machine envelope** — refuses. The reason is the engine's own blocker
  text naming the axis and the overhang in millimetres. **There is no price
  anywhere on the screen.** Say that out loud; it is the point.
- **Missing dimensions** — refuses and asks for the dimensions.

**The URL is now doing work for you.** `*.run.app` in the address bar is visible
proof this is Cloud Run and not localhost, so you no longer have to explain
anything. Show it once at the start and let it sit there.

If you want the strongest version of this shot, put Logs Explorer beside the UI,
filtered to the service, and click *Generate Quote*: about 18 seconds later the
line appears — `POST /api/quote 200 18.2s quote-runner-00005-9fr`. Cause and
effect, in Google's own logs, while the viewer watches.

```
resource.type="cloud_run_revision"
resource.labels.service_name="quote-runner"
httpRequest.requestUrl:"/api/quote"
```

**If it fails mid-shoot**, the service caps at 3 instances and each quote is two
Vertex round trips, so a stall is far more likely to be a cold start than a
fault. Wait it out before you touch anything.

**Fallback, if the URL misbehaves on the day.** `files/scripts/cr_proxy.py`
reaches the service with an identity token and serves it at `localhost:8080`:

```bash
python3 files/scripts/cr_proxy.py "$(gcloud run services describe quote-runner --region us-central1 --format='value(status.url)')" 8080
```

Tested and working, but you should not need it now. Its token lasts about an
hour; restart it rather than debugging the app.

---

## Part 4 — Your task list, in order

1. **Clean the two directories** (commands in Part 3, before you start).
2. **Deploy to Cloud Run — done.** Revision `quote-runner-00005-9fr` is live.
   The org policy that blocked public access was overridden, `allUsers` now
   holds `roles/run.invoker`, and the URL answers unauthenticated requests.
   Shot 6 uses it directly.
3. **Read the 15 newer eval cases.** You're the only person who can say whether
   a 210-minute PETG enclosure matches your machines. The oracle proves they're
   *consistent*, not that they're *real*.
4. **Record shots 2, 3 and 4 first** — they need no cloud credentials and no
   network. If the deploy fights you, you still have three quarters of a video.
5. **Record shot 5** from the Console — no URL required.
6. **Record shot 6 last**, from the public URL in an incognito window, with one
   throwaway quote already fired to clear the cold start. It is the only shot
   that makes a live model call, so it goes last — after everything that cannot
   fail is already in the can.
7. **The devpost is already rewritten.** It now describes one LlmAgent with two
   tools, the deterministic engine and the UI, carries the current
   `0.3.0-derived-tew` naive baseline, and states plainly that there is no
   current GEPA measurement. Read it once before you script the voiceover so
   you do not narrate a claim it no longer makes.

## Part 5 — Questions a judge will ask

**"Why does your agent quote at minimum margin?"**
Because our judge is flat above the floor — it's indifferent between the floor
and 50% above it, so the optimiser sat on the boundary. Documented as limitation
3. A judge that rewards margin captured, not just margin cleared, is the next
iteration.

**"How do you know the improvement is real?"**
On the dev set, we don't — it's one case, and our re-run puts it back at the
seed's score. We say that in the README. The result that *is* real is the
gameable run changing nothing at all across six generations.

**"Isn't the gameable judge a strawman?"**
It's four checks that are each individually defensible, joined with OR, that
never cost the job. That's not a strawman, that's a Tuesday. And we didn't
tune it to lose — it scores our baseline at 96%.

**"Where do your rates come from?"**
A real workshop cost calculator, ported into `derive_rate_card.py`. Every rate
traces to a salary, a replacement value, or a utility bill. We found two errors
in the source workbook doing it.

**"What's not real?"**
Material costs, and the agent can't read attachments yet — filenames only.
Both in the README under Known limitations.

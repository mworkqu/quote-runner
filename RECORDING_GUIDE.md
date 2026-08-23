# Quote Runner — where the project stands, and how to record it

## Part 1 — Where you actually are

**The build is finished.** Everything in the README tree exists and runs. There
is no code left to write before you record.

| Piece | State |
|---|---|
| `costing/` — engine, feasibility, judges, tool surface | done, 15/15 golden tests |
| `costing/rate_card.json` | derived from your workshop cost model, not guessed |
| `evals/` — 25 cases, 8 held out, oracle + harness | done, oracle 100% |
| `agent/` — ADK agent, tools, seed prompt | done |
| `gepa/` — coach, loop, both judges | done, both real runs complete |
| `server.py` — `/healthz`, `/quote`, `/eval` | written, **not yet deployed** |
| `scripts/load_results.py`, `gepa_curve.sql` | done, dry-run verified |

**The only thing not done is the Cloud Run deploy.** That is your one remaining
technical task and it is the "runs on Google Cloud" evidence.

### The numbers you have

| | dev honest (n=17) | held-out honest (n=8) |
|---|---|---|
| naive baseline | 20% overall (96% gameable) | — |
| seed agent, gen 0 | 64.7% | 62.5% |
| honest-optimised, gen 2 | 70.6% at selection, **64.7% on re-run** | 75% |
| gameable-optimised | 52.9% | 62.5% — *seed, never rewritten* |

---

## Part 2 — What to claim, and what not to

Read this before you script anything. Your own results files contain a fact
that will sink you if a judge finds it and you didn't mention it first.

### Claims that are bulletproof — lead with these

**A. The naive baseline gap: honest 20%, gameable 96%.**
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

Generation 2 won selection at 70.6%. At the end of the run the loop
re-evaluates the winner, and the same prompt scored 64.7% — exactly the seed's
score. One dev case is worth 5.88 points. The improvement is one case. The
variance is one case.

Do not say "GEPA improved the agent by six points." Say this instead:

> "On the dev set the lift is one case, and our own re-run puts it back at the
> seed's score — at 17 cases we can't separate signal from sampling noise, and
> we say so in the README. What isn't noise is the other run: optimising
> against the gameable judge changed the prompt zero times in six generations,
> because a metric already reading 100% has no gradient to give."

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

> "Fifteen tests. One of them asserts that this function never grows a price
> argument. Another asserts margin is computed on revenue, not as markup on
> cost — which is the exact error we found in our own workshop's spreadsheet."

---

### Shot 4 — the GEPA divergence (45s)

Side-by-side diff on screen:

```bash
diff -u files/gepa/prompts/gen_0.txt files/gepa/prompts/honest/gen_2.txt
```

> "Nobody wrote these rules. The coach only ever sees the enquiry, what the
> agent answered, and why the judge failed it — never the rate card, never
> ground truth. From that it derived: price to the floor, don't pad lead times,
> and if a CAD file is attached the geometry is probably knowable."

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

**Prerequisite — do this before recording:**

```bash
export GOOGLE_CLOUD_PROJECT=your-project
export GOOGLE_CLOUD_LOCATION=us-central1
export GOOGLE_GENAI_USE_VERTEXAI=TRUE
gcloud auth application-default login
python3 scripts/verify_vertex.py     # must pass all 5
./deploy.sh your-project us-central1
```

Then on camera, with the Cloud Trace console open in a second window:

```bash
curl -s $URL/healthz | python3 -m json.tool
curl -s -X POST $URL/eval | python3 -c 'import json,sys; d=json.load(sys.stdin); print(d["n_passed"], "/", d["n_cases"], "=", d["honest_score"])'
```

> "Same agent object, same judge, same 25 cases — locally, inside the
> optimisation loop, and behind Cloud Run. There is no eval-only code path, so
> there's no chance of scoring something other than what ships."

Let Cloud Trace fill up in the background while `/eval` runs. That is the shot.

---

## Part 4 — Your task list, in order

1. **Clean the two directories** (commands in Part 3, before you start).
2. **Deploy to Cloud Run.** The only unfinished technical work. Run
   `verify_vertex.py` first — it fails fast on the two things that eat a
   weekend, wrong model id and broken function calling.
3. **Read the 15 newer eval cases.** You're the only person who can say whether
   a 210-minute PETG enclosure matches your machines. The oracle proves they're
   *consistent*, not that they're *real*.
4. **Record shots 2, 3 and 4 first** — they need no cloud credentials and no
   network. If the deploy fights you, you still have three quarters of a video.
5. **Record shot 5 last**, once the URL is live.
6. **Update the devpost.** The "Challenges" and "What we learned" sections
   should now carry the noise finding and the caught overfit. Both are more
   interesting than a clean win, and both are already written up in the README's
   new *Known limitations* section — lift the text from there.

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

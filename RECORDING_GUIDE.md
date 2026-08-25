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
| `server.py` — `/healthz`, `/quote`, `/eval` | deployed, revision `quote-runner-00001-wnf` live |
| `scripts/load_results.py`, `gepa_curve.sql` | done, dry-run verified |

**The Cloud Run deploy is done.** Revision `quote-runner-00001-wnf` serves 100%
of traffic in `us-central1` and answers real enquiries — that is your "runs on
Google Cloud" evidence, already in hand.

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
diff -u gepa/prompts/gen_0.txt gepa/prompts/honest/gen_2.txt
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

**Filmed entirely in the Cloud Console.** No local terminal, no curl, no live
model call that can fail while you are recording.

**Why this changed.** The service deployed and reports `Ready`, but the
organisation enforces Domain Restricted Sharing
(`constraints/iam.allowedPolicyMemberDomains`), so `allUsers` cannot be granted
`roles/run.invoker` and the public URL refuses unauthenticated requests. That is
an org policy, not a defect in the build — and the Console shows the same facts
without the fight. Do not burn recording time on it.

**Already done, nothing to run:**

- deployed revision `quote-runner-00001-wnf`, region `us-central1`
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

**Check before you script it:** Cloud Trace is probably **empty**. Tracing only
initialises inside `server.py` on Cloud Run, and no request ever reached that
container; the GEPA runs ran locally and exported no spans. Open Cloud Trace
first — if it is blank, leave it out. The Vertex AI metrics page carries this
shot on its own.

**Shots 2–4 can be filmed in the same window.** Cloud Shell is part of the
Console, so the whole video can be one continuous browser recording:

```bash
cd ~/quote-runner && git pull && cd files
python3 scripts/show_divergence.py      # shot 2
sed -n '47,72p' agent/tools.py          # shot 3
cat gepa/prompts/honest/gen_2.txt       # shot 4
python3 -m evals.harness --validate     # supporting: the cases are honest
```

All four read saved results only — no Vertex spend, nothing that can fail live.

---

## Part 4 — Your task list, in order

1. **Clean the two directories** (commands in Part 3, before you start).
2. **Deploy to Cloud Run — done.** Revision `quote-runner-00001-wnf` is live.
   The public URL is blocked by org-level Domain Restricted Sharing; shot 5 uses
   the Console instead and loses nothing. Do not reopen this.
3. **Read the 15 newer eval cases.** You're the only person who can say whether
   a 210-minute PETG enclosure matches your machines. The oracle proves they're
   *consistent*, not that they're *real*.
4. **Record shots 2, 3 and 4 first** — they need no cloud credentials and no
   network. If the deploy fights you, you still have three quarters of a video.
5. **Record shot 5 last**, from the Console — no URL required.
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

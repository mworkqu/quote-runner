# Quote Runner — Claude Code handoff

> **Historical.** These are the build tasks exactly as they were issued, kept
> as a record of how the project was assembled. Figures quoted inside them
> were current when written and are not maintained — several are superseded,
> including case counts, baseline scores and the rate card version. Nothing
> here should be read as a claim about the system today. For that, read
> `files/README.md`.

Paste each task below into Claude Code as a **separate** prompt, in order.
Task 0 is context — paste it once at the start of the session, then the numbered
tasks one at a time. Do not paste them all at once; each ends with a check that
must pass before the next begins.

---

## Task 0 — context (paste first, expect no code changes)

> Read `files/README.md`, then `files/costing/`, `files/evals/`, `files/agent/`
> and `files/scripts/derive_rate_card.py`. Do not change anything yet — reply
> with a one-paragraph summary of what the project is and what the honest judge
> checks, so I know you have the shape of it.
>
> Context you need that is not obvious from the files:
>
> - This is a hackathon build for a Doha fabrication workshop. The submission
>   track is agent autonomy, and the story is that the eval set was built before
>   the agent, so the agent could never be optimised toward a number that does
>   not mean anything.
> - `costing/` is finished and load-bearing. `cost_job()` is pure — no model, no
>   network, no clock — because the judge costs ground truth with the same
>   function the agent's pricing tool costs estimates with. Do not add caching,
>   logging, timestamps or randomness to it.
> - `price_job()` deliberately has **no argument for a price**. The agent
>   supplies grams and machine minutes; money comes back. That constraint lives
>   in a function signature rather than a prompt because GEPA is allowed to
>   rewrite prompts. Never add a price, cost, or margin argument to it, no
>   matter how convenient it looks.
> - `costing/judge.py` contains `gameable_judge`, which is **deliberately
>   broken**. It is the control group for the demo. Do not fix it, do not make
>   it stricter, do not "improve" it.
> - `costing/rate_card.json` is **generated**. Edit
>   `scripts/derive_rate_card.py` and re-run it with `--write`. A hand edit to
>   the JSON is lost the next time anyone regenerates.
> - Three commands must keep working at every step:
>   `python3 demo.py`, `python3 test_costing.py`,
>   `python3 -m evals.harness --validate`.

---

## Task 1 — correct the README

> `files/README.md` was written before the code existed, so several figures in
> it are now wrong. Update it to match what the code actually does. Change only
> what is factually stale — keep the voice, keep the structure, keep every
> design argument intact.
>
> Corrections needed:
>
> 1. The trap table says the laser amortisation case runs `33.05 → 10.12` per
>    unit. It is now `30.00 → 4.49`, and the reason changed: at 10 units the
>    shop's minimum job value binds, and at 500 the job clears the minimum AND
>    drops to the volume margin band. Rewrite that row to say so.
> 2. "Baseline as of the placeholder rate card: **honest 20%, gameable 85%**"
>    → the measured baseline is **honest 20%, gameable 90%** across all 10
>    cases (dev set alone: honest 14%, gameable 100%).
> 3. The "Numbers you have to replace" section is now mostly obsolete. The rate
>    card is derived, not guessed — rewrite that section to point at
>    `scripts/derive_rate_card.py`, and say that the ONLY numbers still
>    undefended are the material costs in `MATERIALS`, which need real supplier
>    invoices.
> 4. The note about `overhead_pct_of_direct` being 12% is out of date. It is now
>    `0.0` and must stay there, because the derived machine rates already absorb
>    rent, power, depreciation and head office. Explain that adding it back
>    double-counts.
> 5. Add `demo.py`, `test_costing.py` and `scripts/derive_rate_card.py` to the
>    file tree at the top if they are missing.
> 6. `evals/cases.py` says "10 of 25 authored, 3 held out" — leave that until
>    Task 2 changes it.
>
> Then run all three commands and paste the output so I can see nothing broke.

---

## Task 2 — author the remaining 15 eval cases

> `files/evals/cases.py` has 10 of 25 cases, 3 of them held out. Author the
> remaining 15, following the existing pattern exactly.
>
> **Method, in this order — do not skip the first step:**
>
> 1. Write the `enquiry` text FIRST, from the client's side, before you know
>    what the answer is. Cases written backwards from a ground-truth `Job` read
>    like exam questions, and an agent optimised against exam questions learns
>    to answer exam questions.
> 2. Then build the ground-truth `Job` that the enquiry describes.
> 3. Then write the `notes` field explaining what the case catches and why an
>    agent would get it wrong.
>
> **Hard rules:**
>
> - Every quotable case needs `part_bbox_mm`. A missing bbox is a hard blocker
>   by design, so omitting it silently converts a quotable case into an
>   escalation case.
> - Only use `machine_id` and `material_id` values that exist in
>   `costing/rate_card.json`.
> - Sheet materials (`acrylic_3mm`, `acrylic_5mm`, `mdf_6mm`, `ply_9mm`) need
>   `parts_per_sheet`. Mass materials need `material_grams_per_unit`.
> - `cad_minutes` is charged once for the whole job. Everything with a
>   `_per_unit` suffix multiplies by quantity. Do not confuse them.
> - Machine minutes must be physically plausible. A 180x120x65mm PETG enclosure
>   really does take about 210 minutes on a large-format FDM. Do not invent
>   times that make the arithmetic convenient.
>
> **Coverage the existing 10 do not have — build cases for each:**
>
> - At least 3 **multi-operation** jobs, where one `Job` has two or more
>   `Operation` entries (laser-cut blank then milled, printed then finished on a
>   second machine). Nothing in the current set exercises this and the engine
>   supports it.
> - At least 1 job hitting the `incompatible_material` blocker — a machine and
>   material pairing the rate card forbids.
> - At least 2 enquiries with **mixed or wrong units** — inches and millimetres
>   in the same message, or a client saying "cm" and meaning "mm".
> - At least 1 where the client names a material that is wrong for the stated
>   application, and the right answer is still a quote plus a flagged
>   assumption, not an escalation.
> - At least 2 more that should escalate, for reasons that are NOT envelope,
>   stock or missing dimensions — think contradictory requirements, or a
>   quantity so large the queue makes any promise dishonest.
> - At least 2 at the `volume` client band and 2 more at `repeat_client`, with
>   the band signalled naturally in the enquiry text ("same as last time", "send
>   it to the usual account") and never stated outright. Clients never say
>   "please apply the repeat client margin band."
>
> **Held-out split:** end with 8 of 25 held out. Choose the 5 new ones to hold
> back on the principle that a held-out case should be one an optimiser would
> most want to overfit to — the ones where a cheap general strategy (escalate
> more, quote higher, always promise 14 days) would pass it while failing
> others.
>
> **After every 5 cases you add**, run `python3 -m evals.harness --validate`.
> The oracle must stay at 100%. If it drops, the case you just wrote is
> internally impossible — fix the case, never the harness, never the judge.
>
> When all 15 are in, run:
> ```
> python3 -m evals.harness --validate
> python3 -m evals.harness --naive --held-out --no-write
> python3 test_costing.py
> ```
> and paste all three outputs. Then update the `10 of 25 authored, 3 held out`
> line in `evals/cases.py`, `evals/harness.py` and `README.md` to the real
> numbers.

---

## Task 3 — the GEPA optimisation loop

> Build `files/gepa/` next to `agent/`. It optimises the agent's instruction
> against the eval set.
>
> ```
> gepa/
>   __init__.py
>   coach.py        proposes a rewritten instruction from failure traces
>   loop.py         the optimisation loop + CLI
>   prompts/        gen_0.txt (a copy of SEED), gen_1.txt, gen_2.txt, ...
> ```
>
> **Requirements:**
>
> - The ONLY thing it rewrites is the instruction string. It never touches
>   `costing/`, `evals/cases.py`, or the judge. Assert this — if a generation
>   would modify anything outside `gepa/prompts/`, fail loudly.
> - Each generation writes `gepa/prompts/gen_<n>.txt`. `agent/prompt.py` `SEED`
>   stays as generation 0 forever and is never edited, so generations can be
>   diffed and rolled back.
> - Score with `evals.harness.run_eval` over `DEV_CASES` only. The held-out
>   cases must not be touched during optimisation. Make that structurally
>   impossible, not merely discouraged — the loop should not have access to
>   `HELD_OUT_CASES` at all.
> - The coach sees, per failing case: the enquiry, what the agent returned, and
>   the honest judge's `reasons`. It must NOT see the ground-truth `Job`. If the
>   coach can see ground truth it will write prompts that memorise the eval set.
> - Ship a second entry point, `--judge gameable`, that runs the identical loop
>   scored by `gameable_judge` instead. Both write their curve to
>   `evals/results/`. That divergence is the demo's centrepiece, so the two runs
>   must be directly comparable — same seeds, same generation count, same cases.
> - After the loop finishes, score the best generation against the held-out
>   cases ONCE and report both numbers. Never loop on that result.
>
> Include a `--dry-run` that exercises the whole loop with a stub coach and a
> stub agent so the plumbing can be tested without spending Vertex quota. Show
> me that working before anything real runs.

---

## Task 4 — results into BigQuery

> Add `files/scripts/load_results.py`.
>
> `evals/results/*.json` already carry a summary plus flat per-case rows. Write
> a loader that flattens every row into one BigQuery table with columns for:
> run label, timestamp, rate card version, generation number (parse it from the
> label), case id, held_out flag, tags, honest pass, gameable pass, price
> quoted, price floor, true cost, promised lead days, estimated lead days,
> escalated, parse_error, priced_without_tool, and the first honest-judge
> reason.
>
> Requirements:
> - Idempotent. Re-running on the same results directory must not duplicate
>   rows — key on (run label, case id).
> - `--dry-run` prints the schema and the first five rows without touching GCP,
>   so it can be checked with no credentials.
> - Read the project from `GOOGLE_CLOUD_PROJECT`, dataset and table from flags
>   with sensible defaults.
> - Add `google-cloud-bigquery` to `requirements.txt`.
>
> Then write one SQL query, saved as `scripts/gepa_curve.sql`, that returns
> honest score and gameable score per generation per judge — the exact query
> behind the chart in the demo video.

---

## Task 5 — final check before recording

> Run every entry point in the repo from a clean checkout and confirm each one
> works, then give me a single table of results:
>
> ```
> python3 scripts/derive_rate_card.py            # dry run, prints derivation
> python3 demo.py
> python3 test_costing.py
> python3 -m evals.harness --validate
> python3 -m evals.harness --naive --held-out --no-write
> python3 scripts/load_results.py --dry-run
> python3 gepa/loop.py --dry-run
> ```
>
> Then check three things I care about specifically and report on each:
>
> 1. Grep the whole repo for anywhere `case.job` or ground truth could reach an
>    agent. The only legitimate readers are `evals/harness.oracle_quote`,
>    `score_case`, and the judges. Confirm there is no other path.
> 2. Confirm `price_job` still has no price-like argument, and that
>    `list_capabilities()` still leaks no rates or margins.
> 3. Confirm `costing/` imports nothing from `agent/`, `evals/` or `gepa/` —
>    the dependency arrow points one way only, and if it ever reverses the judge
>    can be influenced by the thing it is judging.
>
> Do not fix anything in this task. Report only. I will decide what to fix.

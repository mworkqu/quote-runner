"""Re-print the saved GEPA runs as one side-by-side comparison.

Reads evals/results/*.json ONLY. No model call, no Vertex quota, and no
re-scoring of the held-out set -- this displays what the two runs already
found. Safe to run on camera as many times as you like.

    python3 scripts/show_divergence.py
"""
from __future__ import annotations

import glob
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
RESULTS = sys.argv[1] if len(sys.argv) > 1 else os.path.join(HERE, "..", "evals", "results")


def latest(pattern: str):
    hits = sorted(glob.glob(os.path.join(RESULTS, pattern)))
    return json.load(open(hits[-1], encoding="utf-8")) if hits else None


honest = latest("gepa-honest-2*.json")
gameable = latest("gepa-gameable-2*.json")
h_hold = latest("gepa-honest-holdout-gen*.json")
g_hold = latest("gepa-gameable-holdout-gen*.json")

if not all((honest, gameable, h_hold, g_hold)):
    sys.exit(f"no result files found in {RESULTS} -- run the GEPA loop first")

W = 78
print()
print("=" * W)
print("  QUOTE RUNNER  --  what the optimiser learns depends on the ruler")
print(f"  gemini-3.5-flash / Vertex AI   seed {honest['seed']}   "
      f"{honest['generations']} generations   {len(honest['dev_case_ids'])} dev cases")
print("=" * W)
print()
print("   OPTIMISING THE HONEST JUDGE        |   OPTIMISING THE GAMEABLE PROXY")
print("   deliverable AND profitable         |   'would the client accept it?'")
print("   " + "-" * 33 + "  |   " + "-" * 33)

for a, b in zip(honest["curve"], gameable["curve"]):
    def fmt(r, key):
        tag = "seed" if r["generation"] == 0 else ("ACCEPTED" if r["accepted"] else "rejected")
        return f"gen {r['generation']}  {r[key]:6.1%}  {tag:<9}"
    print(f"   {fmt(a, 'dev_honest'):<33}  |   {fmt(b, 'dev_gameable'):<33}")

print("   " + "-" * 33 + "  |   " + "-" * 33)


def _sel_and_rescore(run, curve_key, rescore_key):
    """The score the winning generation was CHOSEN on, and optimise()'s
    end-of-run re-evaluation of that same instruction. Where they differ, the
    difference is sampling noise on a prompt that never changed."""
    best = run["best_generation"]
    sel = next(r[curve_key] for r in run["curve"] if r["generation"] == best)
    return sel, run[rescore_key]


h_sel, h_re = _sel_and_rescore(honest, "dev_honest", "best_dev_honest")
g_sel, g_re = _sel_and_rescore(gameable, "dev_gameable", "best_dev_gameable")

h_lines = [f"BEST: gen {honest['best_generation']}",
           f"  selection score  {h_sel:6.1%}",
           f"  re-score         {h_re:6.1%}"]
g_lines = [f"BEST: gen {gameable['best_generation']} (seed, untouched)",
           f"  selection score  {g_sel:6.1%}",
           f"  re-score         {g_re:6.1%}"]
for _h, _g in zip(h_lines, g_lines):
    print(f"   {_h:<33}  |   {_g}")
print()
print("   Selection score is what that generation scored when it was chosen.")
print("   Re-score is the same instruction re-evaluated at the end of the run.")
print()
print("=" * W)
print("  HELD-OUT  --  8 cases, scored ONCE, never optimised against")
print("=" * W)
print()
print(f"   optimised on the honest judge     honest {h_hold['honest_score']:5.1%}"
      f"    gameable {h_hold['gameable_score']:5.1%}")
print(f"   optimised on the gameable proxy   honest {g_hold['honest_score']:5.1%}"
      f"    gameable {g_hold['gameable_score']:5.1%}")
print()
gap = (h_hold["honest_score"] - g_hold["honest_score"]) * 100
proxy_own = g_hold["gameable_score"]

if proxy_own >= 1.0:
    print("   The proxy scores a perfect 100% on its own ruler.")
else:
    print(f"   The proxy scores {proxy_own:.1%} on its own ruler.")

if abs(gap) < 0.05:
    # Equal to the resolution of this set. Claiming a divergence here would be
    # asserting something the numbers do not support.
    print("   On the ruler that decides whether the shop stays solvent both arms")
    print(f"   score the same ({h_hold['honest_score']:.1%}).")
    print("   The divergence is NOT detectable on this held-out set.")
elif gap > 0:
    print("   On the ruler that decides whether the shop stays solvent it is")
    print(f"   {gap:.1f} points WORSE than the honest-trained prompt.")
    if proxy_own >= 1.0:
        print("   A perfect score and a bankrupt workshop.")
else:
    print("   On the ruler that decides whether the shop stays solvent it is")
    print(f"   {abs(gap):.1f} points BETTER than the honest-trained prompt.")
    print("   That is the opposite of the expected divergence, and this run is")
    print("   not evidence for it.")
print()
print("=" * W)
print()

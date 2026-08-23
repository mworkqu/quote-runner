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
print(f"   BEST: gen {honest['best_generation']:<25}|   "
      f"BEST: gen {gameable['best_generation']} (seed, untouched)")
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
print("   The proxy scores a perfect 100%. On the ruler that decides whether")
print(f"   the shop stays solvent it is {gap:.1f} points WORSE.")
print("   A perfect score and a bankrupt workshop.")
print()
print("=" * W)
print()

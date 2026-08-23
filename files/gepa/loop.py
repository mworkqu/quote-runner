"""The GEPA optimisation loop and its CLI.

It evolves ONE string — the agent's instruction — against the eval set, and
nothing else. The costing engine, the cases and the judge are read-only ground
that the loop is structurally prevented from touching.

FOUR INVARIANTS, each enforced rather than asked for:

1. ONLY THE INSTRUCTION CHANGES. Every generation is written to
   `gepa/prompts/gen_<n>.txt` and nowhere else. Before the loop starts we hash
   `costing/`, `evals/cases.py`, `evals/harness.py` and `agent/prompt.py`; after
   every generation we re-hash them, and a single altered byte aborts the run
   loudly (`ProtectedTree`). `SEED` in `agent/prompt.py` is in that set, so
   generation 0 stays a faithful copy of it forever and generations stay
   diffable and rollback-able.

2. THE LOOP NEVER SEES HELD-OUT CASES. `optimise()` is handed `DEV_CASES` and
   asserts none of them are held out. It does not import `HELD_OUT_CASES`; that
   name is imported inside `holdout_score()` only, a separate one-shot function
   the loop never calls. The held-out set is not withheld by discipline, it is
   out of scope.

3. THE COACH NEVER SEES GROUND TRUTH. Failures are handed to the coach as
   `FailureTrace` (enquiry + agent output + judge reasons). There is no `Job` in
   that shape, so there is nothing to leak. See `coach.py`.

4. HELD-OUT IS SCORED ONCE. After the loop settles on a best generation,
   `holdout_score()` scores it against the held-out cases a single time and
   reports the number. It is never fed back into selection.

`--judge gameable` runs the identical loop selecting on the gameable judge
instead of the honest one. Same seed, same generation count, same DEV_CASES, so
the two curves are directly comparable — that divergence is the whole demo.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import re
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Sequence

# Run either way: `python3 -m gepa.loop` or `python3 gepa/loop.py`.
# The script-path form puts gepa/ on sys.path instead of the repo root, so
# `costing` and `evals` would not resolve. Every other entry point in this repo
# runs as a plain script; making this one -m-only is a trap for whoever is
# recording the demo at 2am. `scripts/verify_vertex.py` does the same thing.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from costing.judge import QuoteUnderTest  # noqa: E402
from costing.models import RateCard  # noqa: E402
from evals.cases import DEV_CASES, Case  # noqa: E402
from evals.harness import RESULTS_DIR, EvalRun, run_eval  # noqa: E402

# Absolute, not `from .coach` — a relative import cannot resolve when this file
# is run as a script rather than as a module of the gepa package.
from gepa.coach import Coach, FailureTrace, LlmCoach, StubCoach  # noqa: E402

__all__ = [
    "PROMPTS_DIR",
    "ProtectedTree",
    "stub_agent_fn",
    "optimise",
    "holdout_score",
    "OptimizeResult",
    "main",
]

ROOT = Path(__file__).resolve().parent.parent
PROMPTS_DIR = Path(__file__).resolve().parent / "prompts"

AgentFn = Callable[[str, list[str]], dict]
AgentFactory = Callable[[str], AgentFn]

JUDGES = ("honest", "gameable")
_METRIC = {"honest": "passed", "gameable": "passed_gameable"}
_REASONS = {"honest": "honest", "gameable": "gameable"}


# ---------------------------------------------------------------------------
# Invariant 1 — nothing outside gepa/prompts/ may change
# ---------------------------------------------------------------------------


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


@dataclass
class ProtectedTree:
    """A hash snapshot of everything the loop is forbidden to modify.

    `verify()` is called after every generation. If any protected file changed,
    the optimiser did something it must never do, and we stop before it can be
    mistaken for a real result.
    """

    hashes: dict[str, str]

    @staticmethod
    def _paths() -> list[Path]:
        paths = list((ROOT / "costing").glob("*.py"))
        paths += [
            ROOT / "costing" / "rate_card.json",
            ROOT / "evals" / "cases.py",
            ROOT / "evals" / "harness.py",
            ROOT / "agent" / "prompt.py",  # SEED lives here and must never move
        ]
        return [p for p in paths if p.exists()]

    @classmethod
    def snapshot(cls) -> "ProtectedTree":
        return cls({str(p): _sha(p) for p in cls._paths()})

    def verify(self) -> None:
        for path, digest in self.hashes.items():
            current = _sha(Path(path))
            if current != digest:
                raise RuntimeError(
                    "GEPA modified a protected file outside gepa/prompts/: "
                    f"{path}\nThe optimiser may only write gepa/prompts/*.txt. "
                    "Aborting so a corrupted run cannot be mistaken for a result."
                )


def _write_generation(n: int, text: str, judge: str) -> Path:
    """Write <judge>/gen_<n>.txt, and refuse to write anywhere but gepa/prompts/.

    NAMESPACED BY JUDGE, and that is not cosmetic. Both loops used to write to a
    single `gepa/prompts/gen_<n>.txt`, so whichever ran second silently
    overwrote the other's lineage. The gameable run rewrites nothing (it has no
    failures to reflect on), so running it after the honest run replaced six
    genuinely evolved prompts with six identical copies of the seed — including
    the winner. The scores survived in the curve file; the artefact that earned
    them did not.
    """
    judge_dir = (PROMPTS_DIR / judge).resolve()
    judge_dir.mkdir(parents=True, exist_ok=True)
    path = (judge_dir / f"gen_{n}.txt").resolve()
    if path.parent != judge_dir or PROMPTS_DIR.resolve() not in path.parents:
        raise RuntimeError(f"refusing to write a generation outside prompts/: {path}")
    path.write_text(text, encoding="utf-8")
    return path


def _load_seed() -> str:
    """Read SEED without importing the `agent` package (which pulls in Vertex).

    `agent/prompt.py` is a pure string module, so we can exec it in isolation
    and keep the dry-run free of the Google stack.
    """
    p = ROOT / "agent" / "prompt.py"
    spec = importlib.util.spec_from_file_location("_gepa_seed_isolated", p)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod.SEED


def ensure_gen0() -> str:
    """Generation 0 is a copy of SEED. Write it if missing or drifted, and never
    the other way around — SEED itself is protected and never edited."""
    seed = _load_seed()
    path = PROMPTS_DIR / "gen_0.txt"
    if not path.exists() or path.read_text(encoding="utf-8") != seed:
        PROMPTS_DIR.mkdir(parents=True, exist_ok=True)
        path.write_text(seed, encoding="utf-8")
    return seed


# ---------------------------------------------------------------------------
# The stub agent — deterministic, offline, driven by the instruction
# ---------------------------------------------------------------------------
#
# It exists so --dry-run can move a real score without a model. Its baseline is
# the vibes agent from evals.harness.naive_quote (copied here, not imported from
# a private name, so the two stay legibly identical): read a quantity, guess a
# price, promise a fixed lead, never escalate. On top of that it honours the
# ESCALATE-WHEN: rules the stub coach writes into the instruction — a purely
# text-triggered escalation on the visible enquiry, never on ground truth.

_DIMENSION = re.compile(
    r"\b\d{1,4}(?:\.\d+)?\s*(?:x|×)\s*\d{1,4}(?:\.\d+)?(?:\s*(?:x|×)\s*\d{1,4}(?:\.\d+)?)?\s*(?:mm)?\b"
    r"|\b\d{1,4}(?:\.\d+)?\s*mm\b",
    re.I,
)
_INTEGER = re.compile(r"\b(\d{1,4})\b")

_NAIVE_RATE_PER_UNIT = 45
_NAIVE_FLOOR = 250
_NAIVE_LEAD_DAYS = 7


def _escalate_rules(instruction: str) -> list[str]:
    rules = []
    for line in instruction.splitlines():
        s = line.strip()
        if s.upper().startswith("ESCALATE-WHEN:"):
            phrase = s.split(":", 1)[1].strip().lower()
            if phrase:
                rules.append(phrase)
    return rules


def stub_agent_fn(instruction: str) -> AgentFn:
    """Build a deterministic agent whose behaviour depends on the instruction."""
    rules = _escalate_rules(instruction)

    def agent(enquiry: str, attachments: list[str]) -> dict:
        text = enquiry.lower()
        if any(phrase in text for phrase in rules):
            # Emit the harness's normalised schema directly (the stub does not go
            # through agent.parse_quote), so `escalated` is the key it reads.
            return {
                "price": 0.0,
                "promised_lead_days": None,
                "escalated": True,
                "question": "This needs a detail confirmed before I can quote it.",
                "reasoning": "An escalation rule in the instruction matched this enquiry.",
            }

        if not _DIMENSION.search(enquiry):
            # No dimensions to reason from — even vibes need an input.
            return {"price": 0.0, "promised_lead_days": None, "escalated": False, "reasoning": ""}

        quantity = 1
        for m in _INTEGER.finditer(_DIMENSION.sub(" ", enquiry)):
            n = int(m.group(1))
            if 1 <= n <= 1000:
                quantity = n
                break
        price = max(_NAIVE_FLOOR, _NAIVE_RATE_PER_UNIT * quantity)
        return {
            "price": float(price),
            "promised_lead_days": _NAIVE_LEAD_DAYS,
            "escalated": False,
            "reasoning": (
                f"Based on our standard rates, {quantity} unit(s) is about {price}. "
                f"We can typically turn this around in {_NAIVE_LEAD_DAYS} working days."
            ),
        }

    return agent


# ---------------------------------------------------------------------------
# Scoring plumbing
# ---------------------------------------------------------------------------


def _quote_fn(agent_fn: AgentFn):
    """Wrap an agent in the harness's (Case -> QuoteUnderTest) contract.

    The agent is called with the VISIBLE half of the case only. `case.job` never
    enters this function — the same ground-truth boundary the harness enforces.
    """

    def run(case: Case) -> QuoteUnderTest:
        visible = case.for_agent()
        return QuoteUnderTest.from_agent_output(agent_fn(visible["enquiry"], visible["attachments"]))

    return run


def _evaluate(
    agent_fn: AgentFn,
    cases: Sequence[Case],
    card: RateCard,
    *,
    label: str = "gepa-eval",
    write: bool = False,
) -> EvalRun:
    """Score one instruction over `cases`.

    Generation evaluations pass a label of the form `gepa-<judge>[-dry]-gen<n>`
    and `write=True`, so every generation lands in `evals/results/` as a normal
    per-case row set — the same shape `--naive` writes. That is what makes the
    curve reconstructible in BigQuery: `scripts/load_results.py` parses the
    generation and the optimisation target straight out of the label, and
    `scripts/gepa_curve.sql` groups by them. The curve JSON stays a human
    summary; the per-case rows are the queryable artefact.
    """
    return run_eval(_quote_fn(agent_fn), cases, label=label, card=card, write=write)


def _metric(run: EvalRun, judge: str) -> float:
    key = _METRIC[judge]
    rows = run.rows
    return round(sum(1 for r in rows if r[key]) / len(rows), 4) if rows else 0.0


def _failures(run: EvalRun, by_id: dict[str, Case], judge: str) -> list[FailureTrace]:
    """Build the coach's view of the failures — visible signals + reasons only.

    This is the single place a failing case is unpacked for the coach, so it is
    the one line to audit for leaks. It reads `case.enquiry`/`case.attachments`
    (the visible half) and the judge's `reasons`, and never `case.job`.
    """
    key = _METRIC[judge]
    reason_key = _REASONS[judge]
    out: list[FailureTrace] = []
    for row in run.rows:
        if row[key]:
            continue
        case = by_id[row["case_id"]]
        out.append(
            FailureTrace(
                case_id=case.id,
                enquiry=case.enquiry,
                attachments=tuple(case.attachments),
                returned=row["quote"],
                reasons=tuple(row[reason_key]["reasons"]),
            )
        )
    return out


# ---------------------------------------------------------------------------
# The loop
# ---------------------------------------------------------------------------


@dataclass
class GenerationRecord:
    generation: int
    prompt_file: str
    dev_honest: float
    dev_gameable: float
    selected_metric: float
    accepted: bool
    best_metric_after: float
    note: str
    failed_case_ids: list[str]

    def as_dict(self) -> dict:
        return self.__dict__


@dataclass
class OptimizeResult:
    judge: str
    seed: int
    generations: int
    dev_case_ids: list[str]
    curve: list[GenerationRecord]
    best_generation: int
    best_instruction: str
    best_dev_honest: float
    best_dev_gameable: float
    rate_card_version: str
    coach: str
    agent: str

    def as_dict(self) -> dict:
        return {
            "label": f"gepa-{self.judge}",
            "judge": self.judge,
            "coach": self.coach,
            "agent": self.agent,
            "seed": self.seed,
            "generations": self.generations,
            "rate_card_version": self.rate_card_version,
            "dev_case_ids": self.dev_case_ids,
            "best_generation": self.best_generation,
            "best_dev_honest": self.best_dev_honest,
            "best_dev_gameable": self.best_dev_gameable,
            # The winning instruction, inline. A prompt file can be overwritten
            # by the next run; a result file is written once and never touched
            # again. If the two ever disagree, this is the one that produced
            # the scores in `curve`.
            "best_instruction": self.best_instruction,
            "curve": [r.as_dict() for r in self.curve],
        }


def _delta_note(old: str, new: str) -> str:
    """A short human label for what a generation changed vs its parent."""
    if new == old:
        return "(no change)"
    old_lines = set(old.splitlines())
    added = [ln.strip() for ln in new.splitlines() if ln.strip() and ln not in old_lines]
    rule = next((a for a in added if a.upper().startswith("ESCALATE-WHEN:")), "")
    return rule or "instruction rewritten"


def optimise(
    agent_factory: AgentFactory,
    coach: Coach,
    cases: Sequence[Case],
    *,
    judge: str,
    generations: int,
    seed: int,
    card: RateCard | None = None,
    run_prefix: str | None = None,
    write_rows: bool = True,
) -> OptimizeResult:
    """Evolve the instruction against `cases`, selecting on `judge`.

    `cases` MUST be dev-only; this asserts it. The function has no reference to
    the held-out set — it cannot score against what it cannot name.

    `run_prefix` names the run in the results files (default `gepa-<judge>`);
    each generation writes `<run_prefix>-gen<n>` per-case rows for BigQuery.
    """
    if judge not in JUDGES:
        raise ValueError(f"judge must be one of {JUDGES}, got {judge!r}")
    if any(c.held_out for c in cases):
        raise AssertionError(
            "optimise() was handed a held-out case. The loop must only ever see "
            "DEV_CASES; held-out scoring is a separate one-shot."
        )

    card = card or RateCard.load()
    guard = ProtectedTree.snapshot()
    by_id = {c.id: c for c in cases}
    prefix = run_prefix or f"gepa-{judge}"

    instruction = ensure_gen0()
    guard.verify()

    base_run = _evaluate(
        agent_factory(instruction), cases, card, label=f"{prefix}-gen0", write=write_rows
    )
    best_instruction = instruction
    best_metric = _metric(base_run, judge)
    best_generation = 0

    curve = [
        GenerationRecord(
            generation=0,
            prompt_file="gen_0.txt",
            dev_honest=_metric(base_run, "honest"),
            dev_gameable=_metric(base_run, "gameable"),
            selected_metric=best_metric,
            accepted=True,
            best_metric_after=best_metric,
            note="seed (copy of SEED)",
            failed_case_ids=base_run.summary()["failed_case_ids"],
        )
    ]

    best_run = base_run
    for n in range(1, generations + 1):
        # The coach reflects on the failures of the current BEST instruction and
        # proposes a rewrite of it.
        parent = best_instruction
        failures = _failures(best_run, by_id, judge)
        proposal = coach.propose(parent, failures, generation=n, seed=seed)
        path = _write_generation(n, proposal, judge)
        guard.verify()  # the coach must not have touched anything but this file

        cand_run = _evaluate(
            agent_factory(proposal), cases, card, label=f"{prefix}-gen{n}", write=write_rows
        )
        cand_metric = _metric(cand_run, judge)
        accepted = cand_metric > best_metric

        if accepted:
            best_instruction, best_metric, best_generation, best_run = proposal, cand_metric, n, cand_run

        curve.append(
            GenerationRecord(
                generation=n,
                # Relative to gepa/prompts/, so the judge lineage is on the
                # record — "gen_1.txt" alone no longer identifies a file.
                prompt_file=str(path.relative_to(PROMPTS_DIR.resolve())).replace("\\", "/"),
                dev_honest=_metric(cand_run, "honest"),
                dev_gameable=_metric(cand_run, "gameable"),
                selected_metric=cand_metric,
                accepted=accepted,
                best_metric_after=best_metric,
                note=_delta_note(parent, proposal),
                failed_case_ids=cand_run.summary()["failed_case_ids"],
            )
        )

    best_run = _evaluate(agent_factory(best_instruction), cases, card)
    return OptimizeResult(
        judge=judge,
        seed=seed,
        generations=generations,
        dev_case_ids=[c.id for c in cases],
        curve=curve,
        best_generation=best_generation,
        best_instruction=best_instruction,
        best_dev_honest=_metric(best_run, "honest"),
        best_dev_gameable=_metric(best_run, "gameable"),
        rate_card_version=card.version,
        coach=getattr(coach, "name", type(coach).__name__),
        agent="stub" if agent_factory is stub_agent_fn else "llm",
    )


# ---------------------------------------------------------------------------
# Invariant 4 — held-out scored exactly once, never looped on
# ---------------------------------------------------------------------------


def holdout_score(
    best_instruction: str,
    agent_factory: AgentFactory,
    *,
    card: RateCard | None = None,
    label: str = "gepa-holdout",
    write: bool = False,
) -> dict:
    """Score one instruction against the held-out cases, a single time.

    The import of HELD_OUT_CASES lives INSIDE this function on purpose: the
    optimisation loop above never has the name in scope, so it cannot score
    against held-out even by accident. This returns a summary and returns it
    once; nothing here feeds back into selection.

    The rows it writes carry `held_out=true`, so the BigQuery curve query
    excludes them from the dev curve by filtering on that flag rather than by
    remembering which labels were holdout runs.
    """
    from evals.cases import HELD_OUT_CASES  # local by design — see docstring

    card = card or RateCard.load()
    run = run_eval(
        _quote_fn(agent_factory(best_instruction)),
        HELD_OUT_CASES,
        label=label,
        card=card,
        write=write,
    )
    return run.summary()


# ---------------------------------------------------------------------------
# Output
# ---------------------------------------------------------------------------


def _write_curve(result: OptimizeResult, dry_run: bool) -> Path:
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    suffix = "-dry" if dry_run else ""
    path = RESULTS_DIR / f"gepa-{result.judge}{suffix}-{stamp}.json"
    payload = result.as_dict()
    payload["dry_run"] = dry_run
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return path


def _print(result: OptimizeResult, held: dict, curve_path: Path) -> None:
    rule = "-" * 72
    print(f"\n{rule}")
    print(
        f"  GEPA  judge={result.judge}  seed={result.seed}  "
        f"generations={result.generations}  dev_cases={len(result.dev_case_ids)}"
    )
    print(f"  coach={result.coach}  agent={result.agent}  rate card {result.rate_card_version}")
    print(rule)
    for r in result.curve:
        mark = "  seed  " if r.generation == 0 else ("accepted" if r.accepted else "rejected")
        print(
            f"  gen {r.generation:<2} {r.prompt_file:<11} "
            f"dev honest {r.dev_honest:6.1%}  gameable {r.dev_gameable:6.1%}   "
            f"{mark}   {r.note}"
        )
    print(rule)
    print(
        f"  BEST = gen {result.best_generation}  "
        f"dev honest {result.best_dev_honest:.1%}  gameable {result.best_dev_gameable:.1%}"
    )
    print(f"  wrote {curve_path}")
    print(rule)
    print("  HELD-OUT (scored once, never optimised against):")
    print(
        f"    best gen {result.best_generation}:  "
        f"honest {held['honest_score']:.1%}  gameable {held['gameable_score']:.1%}  "
        f"(n={held['n_cases']})"
    )
    print(rule + "\n")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main(argv: Sequence[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        prog="python3 -m gepa.loop",
        description="Optimise the agent instruction against the eval set (DEV_CASES only).",
    )
    p.add_argument("--judge", choices=JUDGES, default="honest",
                   help="which judge selects the winner (default: honest). "
                        "gameable runs the identical loop for the divergence demo.")
    p.add_argument("--generations", type=int, default=6, help="number of generations (default 6)")
    p.add_argument("--seed", type=int, default=7, help="deterministic seed (default 7)")
    p.add_argument("--dry-run", action="store_true",
                   help="stub coach + stub agent; no Vertex quota. Exercises the whole loop. "
                        "Implies --no-write, since stub scores are not results.")
    p.add_argument("--model", default=None, help="model id for a real run (overrides QR_MODEL)")
    p.add_argument("--no-write", action="store_true",
                   help="do not write per-case row files to evals/results/")
    p.add_argument("--write-rows", action="store_true",
                   help="write row files even on a dry run (for testing load_results.py)")
    args = p.parse_args(argv)

    # A dry run is a plumbing test, not a result. Each one used to write
    # 2 x (generations + 1) + 2 files, so ten plumbing tests buried the two real
    # baseline runs under a hundred files of stub output. The rows are labelled
    # `-dry` and `gepa_curve.sql` filters them, so this was never a correctness
    # problem — but a results directory nobody trusts at a glance is one anyway.
    write_rows = args.write_rows or not (args.dry_run or args.no_write)

    card = RateCard.load()

    if args.dry_run:
        agent_factory: AgentFactory = stub_agent_fn
        coach: Coach = StubCoach(seed=args.seed)
    else:
        try:
            from agent import make_agent_fn
        except ImportError as e:
            print(
                f"  cannot import the real agent ({e}).\n"
                "  Install requirements and set the Vertex env vars (see "
                "scripts/verify_vertex.py), or use --dry-run.",
                file=sys.stderr,
            )
            return 1
        agent_factory = lambda instruction: make_agent_fn(instruction, args.model)  # noqa: E731
        coach = LlmCoach(seed=args.seed, **({"model": args.model} if args.model else {}))

    # `-dry` keeps stub-driven rows from being mistaken for real agent runs in
    # the warehouse; the label is the only thing that distinguishes them there.
    prefix = f"gepa-{args.judge}-dry" if args.dry_run else f"gepa-{args.judge}"

    t0 = time.perf_counter()
    result = optimise(
        agent_factory,
        coach,
        DEV_CASES,  # the loop is only ever handed the dev set
        judge=args.judge,
        generations=args.generations,
        seed=args.seed,
        card=card,
        run_prefix=prefix,
        write_rows=write_rows,
    )
    # Invariant 4: one held-out score, after the loop, never fed back.
    held = holdout_score(
        result.best_instruction,
        agent_factory,
        card=card,
        label=f"{prefix}-holdout-gen{result.best_generation}",
        write=write_rows,
    )
    curve_path = _write_curve(result, dry_run=args.dry_run)
    _print(result, held, curve_path)
    print(f"  ({time.perf_counter() - t0:.2f}s)\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

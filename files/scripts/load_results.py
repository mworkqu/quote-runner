"""Flatten evals/results/*.json into one BigQuery table.

    python3 scripts/load_results.py --dry-run          # schema + 5 rows, no GCP
    python3 scripts/load_results.py                    # load into BigQuery
    python3 scripts/load_results.py --dataset quoting --table eval_results

WHAT THIS IS FOR

Every eval run writes a JSON file of flat per-case rows. One row per (run, case)
is exactly the grain a warehouse wants, so the whole history — naive baselines,
oracle validations, and every GEPA generation — lands in a single table you can
group, pivot and chart. `scripts/gepa_curve.sql` is the query behind the demo
chart, and it is a GROUP BY over this table and nothing else.

IDEMPOTENCY

Re-running on the same directory must not duplicate rows. The natural key is
(run_label, case_id): a run label names one scoring pass, and a case appears in
it exactly once. Rows are staged into a temporary table and then MERGEd on that
key — matched rows are updated, new rows inserted. Loading the same directory
twice is a no-op the second time, so this is safe to wire into a cron or to run
after every eval without thinking about it.

WHICH FILES ARE READ

Only files carrying a `results` array of per-case rows — what `evals.harness
.run_eval` writes. The GEPA curve files (`gepa-*-2026*.json` with a `curve` key)
are per-generation SUMMARIES, not per-case rows; they are skipped by design and
reported as skipped. Nothing is lost: `gepa.loop` writes each generation's
per-case rows separately under a `gen<n>` label, which is where the curve is
reconstructed from.

GENERATION AND JUDGE COME FROM THE LABEL

A GEPA run labels each generation `gepa-<judge>[-dry]-gen<n>`, so:

    gepa-honest-gen3          -> judge=honest,   generation=3,   dry=false
    gepa-gameable-dry-gen0    -> judge=gameable, generation=0,   dry=true
    gepa-honest-holdout-gen3  -> judge=honest,   generation=3,   held-out rows
    naive-20260822T004041Z    -> judge=NULL,     generation=NULL

`judge` here is the OPTIMISATION TARGET — which judge selected that run's
winner — not the judge that scored the row. Every row carries BOTH scores
(`honest_passed`, `gameable_passed`), because `score_case` always evaluates
both. That is what lets one query show the honest score of a gameable-optimised
run, which is the entire point of the demo.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path
from typing import Any, Iterator, Sequence

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_RESULTS_DIR = ROOT / "evals" / "results"

DEFAULT_DATASET = "quote_runner"
DEFAULT_TABLE = "eval_results"

# gepa-<judge>[-dry][-holdout]-gen<n>
_LABEL = re.compile(
    r"^gepa-(?P<judge>honest|gameable)(?P<dry>-dry)?(?P<holdout>-holdout)?-gen(?P<gen>\d+)$",
    re.I,
)


# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------
#
# Kept as plain tuples so `--dry-run` can print the schema without importing the
# BigQuery client. `_bq_schema()` turns them into SchemaField objects only when
# a real load is happening.

SCHEMA: tuple[tuple[str, str, str], ...] = (
    # name, BigQuery type, mode
    ("run_label", "STRING", "REQUIRED"),
    ("run_started_at", "TIMESTAMP", "NULLABLE"),
    ("rate_card_version", "STRING", "NULLABLE"),
    ("judge", "STRING", "NULLABLE"),          # optimisation target, from the label
    ("generation", "INT64", "NULLABLE"),      # parsed from the label
    ("dry_run", "BOOL", "NULLABLE"),
    ("case_id", "STRING", "REQUIRED"),
    ("held_out", "BOOL", "NULLABLE"),
    ("tags", "STRING", "REPEATED"),
    ("honest_passed", "BOOL", "NULLABLE"),
    ("gameable_passed", "BOOL", "NULLABLE"),
    ("price_quoted", "FLOAT64", "NULLABLE"),
    ("price_floor", "FLOAT64", "NULLABLE"),
    ("true_cost", "FLOAT64", "NULLABLE"),
    ("promised_lead_days", "INT64", "NULLABLE"),
    ("estimated_lead_days", "INT64", "NULLABLE"),
    ("escalated", "BOOL", "NULLABLE"),
    ("parse_error", "BOOL", "NULLABLE"),
    ("priced_without_tool", "BOOL", "NULLABLE"),
    ("honest_reason", "STRING", "NULLABLE"),  # first reason only; the headline
    ("source_file", "STRING", "NULLABLE"),
)

MERGE_KEY = ("run_label", "case_id")


def _bq_schema() -> list:
    from google.cloud import bigquery

    return [bigquery.SchemaField(n, t, mode=m) for n, t, m in SCHEMA]


# ---------------------------------------------------------------------------
# Flattening
# ---------------------------------------------------------------------------


def parse_label(label: str) -> dict[str, Any]:
    """Pull judge / generation / dry / holdout out of a run label.

    Non-GEPA labels (`naive-…`, `oracle-…`) simply have no generation, and that
    is a NULL rather than a zero — a naive baseline is not generation 0 of
    anything, and charting it as such would be a lie.
    """
    m = _LABEL.match(label.strip())
    if not m:
        return {"judge": None, "generation": None, "dry_run": None, "holdout": False}
    return {
        "judge": m.group("judge").lower(),
        "generation": int(m.group("gen")),
        "dry_run": bool(m.group("dry")),
        "holdout": bool(m.group("holdout")),
    }


def _as_int(value: Any) -> int | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _as_float(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def flatten_file(path: Path) -> list[dict[str, Any]]:
    """One results file -> a list of table rows. Returns [] for summary files."""
    doc = json.loads(path.read_text(encoding="utf-8"))
    rows_in = doc.get("results")
    if not isinstance(rows_in, list):
        return []  # a GEPA curve summary, not a per-case row set

    label = str(doc.get("label") or path.stem)
    meta = parse_label(label)
    started = doc.get("started_at")
    version = doc.get("rate_card_version")
    # A file-level dry_run flag wins over the label sniff when present.
    dry = doc.get("dry_run", meta["dry_run"])

    out: list[dict[str, Any]] = []
    for r in rows_in:
        quote = r.get("quote") or {}
        honest = r.get("honest") or {}
        reasons = honest.get("reasons") or []
        out.append(
            {
                "run_label": label,
                "run_started_at": started,
                "rate_card_version": version,
                "judge": meta["judge"],
                "generation": meta["generation"],
                "dry_run": dry,
                "case_id": r.get("case_id"),
                "held_out": bool(r.get("held_out", False)),
                "tags": list(r.get("tags") or []),
                "honest_passed": bool(r.get("passed", False)),
                "gameable_passed": bool(r.get("passed_gameable", False)),
                "price_quoted": _as_float(quote.get("price")),
                "price_floor": _as_float(honest.get("price_floor")),
                "true_cost": _as_float(honest.get("true_cost")),
                "promised_lead_days": _as_int(quote.get("promised_lead_days")),
                "estimated_lead_days": _as_int(honest.get("estimated_lead_days")),
                "escalated": bool(quote.get("escalated", False)),
                "parse_error": bool(quote.get("parse_error", False)),
                "priced_without_tool": bool(quote.get("priced_without_tool", False)),
                "honest_reason": (reasons[0] if reasons else None),
                "source_file": path.name,
            }
        )
    return out


def collect(results_dir: Path) -> tuple[list[dict[str, Any]], list[str]]:
    """Flatten every results file. Returns (rows, skipped_filenames)."""
    rows: list[dict[str, Any]] = []
    skipped: list[str] = []
    for path in sorted(results_dir.glob("*.json")):
        got = flatten_file(path)
        if got:
            rows.extend(got)
        else:
            skipped.append(path.name)
    return rows, skipped


def dedupe(rows: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
    """Last write wins within a single load, on (run_label, case_id).

    Two files can legitimately carry the same label if a run was repeated; the
    MERGE would collapse them anyway, but BigQuery rejects a MERGE whose source
    has duplicate keys, so they are collapsed here first. Files are read in
    sorted order, so "last" is deterministic: the newest timestamped file wins.

    `label_collisions()` reports when this actually merged two different runs,
    because a silent collapse is the kind of thing that makes a chart lie.
    """
    seen: dict[tuple[Any, ...], dict[str, Any]] = {}
    for r in rows:
        seen[tuple(r[k] for k in MERGE_KEY)] = r
    return list(seen.values())


def label_collisions(rows: Sequence[dict[str, Any]]) -> dict[str, list[str]]:
    """Labels that appear in more than one source file.

    `evals.harness.run_eval` labels every naive run "naive" and every oracle run
    "oracle", so two runs a minute apart share a run_label and the MERGE key
    treats them as one run. That is correct per the key, and it is also a real
    way to lose data without noticing, so it is reported rather than hidden.
    GEPA generations are unaffected: each carries its own `gen<n>` label.
    """
    by_label: dict[str, set[str]] = {}
    for r in rows:
        by_label.setdefault(r["run_label"], set()).add(r["source_file"])
    return {label: sorted(files) for label, files in by_label.items() if len(files) > 1}


# ---------------------------------------------------------------------------
# Dry run
# ---------------------------------------------------------------------------


def print_dry_run(
    rows: Sequence[dict[str, Any]],
    skipped: Sequence[str],
    dest: str,
    collisions: dict[str, list[str]] | None = None,
) -> None:
    rule = "-" * 78
    print(f"\n{rule}\n  SCHEMA  {dest}\n{rule}")
    width = max(len(n) for n, _, _ in SCHEMA)
    for name, type_, mode in SCHEMA:
        star = " *" if name in MERGE_KEY else "  "
        print(f"  {name:<{width}}  {type_:<9} {mode:<8}{star}")
    print(f"\n  * = MERGE key: rows are keyed on {MERGE_KEY}, so re-loading is a no-op.")

    print(f"\n{rule}\n  FIRST 5 ROWS  ({len(rows)} total)\n{rule}")
    for row in list(rows)[:5]:
        print()
        for name, _, _ in SCHEMA:
            value = row.get(name)
            if isinstance(value, list):
                value = ",".join(str(v) for v in value) or "-"
            print(f"  {name:<{width}}  {value if value is not None else 'NULL'}")

    if skipped:
        print(f"\n{rule}")
        print(f"  SKIPPED {len(skipped)} file(s) with no per-case rows "
              "(GEPA curve summaries — the per-generation rows are loaded from "
              "their own gen<n> files):")
        for name in skipped:
            print(f"    {name}")

    if collisions:
        print(f"\n{rule}")
        print("  LABEL COLLISIONS — one run_label spanning several files. The MERGE key")
        print("  is (run_label, case_id), so these were collapsed to the newest file:")
        for label, files in collisions.items():
            print(f"    {label}: {', '.join(files)}")
        print("  Harmless for GEPA (every generation has its own gen<n> label). For")
        print("  repeated --naive/--oracle runs it means only the latest is charted.")

    # A quick sanity read on what the curve query will find.
    gens = sorted({(r["judge"], r["generation"]) for r in rows if r["generation"] is not None})
    if gens:
        print(f"\n{rule}\n  GEPA generations present (dev rows are what the curve charts):")
        for judge, gen in gens:
            same = [r for r in rows if r["judge"] == judge and r["generation"] == gen]
            dev = sum(1 for r in same if not r["held_out"])
            held = len(same) - dev
            tail = f"  + {held} held-out" if held else ""
            print(f"    {judge:<9} gen {gen:<3} {dev} dev rows{tail}")
        print("\n  The curve query filters `NOT held_out`, so the one-shot held-out")
        print("  scoring never enters the optimisation curve.")
    print(f"{rule}\n")


# ---------------------------------------------------------------------------
# BigQuery load
# ---------------------------------------------------------------------------


def load_to_bigquery(
    rows: Sequence[dict[str, Any]],
    project: str,
    dataset: str,
    table: str,
) -> int:
    """Stage into a temp table, then MERGE on (run_label, case_id)."""
    from google.cloud import bigquery

    client = bigquery.Client(project=project)
    schema = _bq_schema()

    dataset_ref = bigquery.Dataset(f"{project}.{dataset}")
    dataset_ref.location = os.environ.get("BQ_LOCATION", "US")
    client.create_dataset(dataset_ref, exists_ok=True)

    target_id = f"{project}.{dataset}.{table}"
    client.create_table(bigquery.Table(target_id, schema=schema), exists_ok=True)

    # Staging table: expires on its own so a crashed run leaves no litter.
    staging_id = f"{project}.{dataset}.{table}_staging"
    staging = bigquery.Table(staging_id, schema=schema)
    client.delete_table(staging_id, not_found_ok=True)
    client.create_table(staging)

    job = client.load_table_from_json(
        list(rows),
        staging_id,
        job_config=bigquery.LoadJobConfig(
            schema=schema,
            write_disposition=bigquery.WriteDisposition.WRITE_TRUNCATE,
        ),
    )
    job.result()

    cols = [n for n, _, _ in SCHEMA]
    on = " AND ".join(f"T.{k} = S.{k}" for k in MERGE_KEY)
    updates = ", ".join(f"{c} = S.{c}" for c in cols if c not in MERGE_KEY)
    merge = f"""
    MERGE `{target_id}` T
    USING `{staging_id}` S
    ON {on}
    WHEN MATCHED THEN UPDATE SET {updates}
    WHEN NOT MATCHED THEN INSERT ({', '.join(cols)})
      VALUES ({', '.join('S.' + c for c in cols)})
    """
    merge_job = client.query(merge)
    merge_job.result()
    client.delete_table(staging_id, not_found_ok=True)

    affected = merge_job.num_dml_affected_rows
    return affected if affected is not None else len(rows)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main(argv: Sequence[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        prog="python3 scripts/load_results.py",
        description="Flatten evals/results/*.json into one BigQuery table.",
    )
    p.add_argument("--results-dir", type=Path, default=DEFAULT_RESULTS_DIR,
                   help=f"directory of results JSON (default: {DEFAULT_RESULTS_DIR})")
    p.add_argument("--dataset", default=DEFAULT_DATASET, help=f"BigQuery dataset (default: {DEFAULT_DATASET})")
    p.add_argument("--table", default=DEFAULT_TABLE, help=f"BigQuery table (default: {DEFAULT_TABLE})")
    p.add_argument("--project", default=None, help="GCP project (default: $GOOGLE_CLOUD_PROJECT)")
    p.add_argument("--dry-run", action="store_true",
                   help="print the schema and the first five rows; touches no GCP and needs no credentials")
    args = p.parse_args(argv)

    if not args.results_dir.is_dir():
        print(f"  no results directory at {args.results_dir}", file=sys.stderr)
        return 1

    rows, skipped = collect(args.results_dir)
    collisions = label_collisions(rows)
    rows = dedupe(rows)
    if not rows:
        print(f"  no per-case rows found in {args.results_dir}. Run an eval first:\n"
              "    python3 -m evals.harness --naive\n"
              "    python3 -m gepa.loop --dry-run", file=sys.stderr)
        return 1

    project = args.project or os.environ.get("GOOGLE_CLOUD_PROJECT")

    if args.dry_run:
        dest = f"{project or '<GOOGLE_CLOUD_PROJECT>'}.{args.dataset}.{args.table}"
        print_dry_run(rows, skipped, dest, collisions)
        print(f"  DRY RUN — nothing was sent to BigQuery. {len(rows)} row(s) ready.\n")
        return 0

    if not project:
        print("  GOOGLE_CLOUD_PROJECT is not set (or pass --project).\n"
              "  Use --dry-run to check the schema and rows without credentials.", file=sys.stderr)
        return 1

    try:
        from google.cloud import bigquery  # noqa: F401
    except ImportError:
        print("  google-cloud-bigquery is not installed:  pip install google-cloud-bigquery\n"
              "  Use --dry-run to check the schema and rows without it.", file=sys.stderr)
        return 1

    affected = load_to_bigquery(rows, project, args.dataset, args.table)
    print(f"\n  merged {len(rows)} row(s) into {project}.{args.dataset}.{args.table} "
          f"({affected} affected)")
    if skipped:
        print(f"  skipped {len(skipped)} summary file(s) with no per-case rows")
    print(f"\n  chart it:  bq query --use_legacy_sql=false < scripts/gepa_curve.sql\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

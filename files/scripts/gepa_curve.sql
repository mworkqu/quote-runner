-- gepa_curve.sql — the query behind the chart in the demo video.
--
--     bq query --use_legacy_sql=false < scripts/gepa_curve.sql
--
-- Returns, per generation and per optimisation target: the honest score and the
-- gameable score on the dev set.
--
-- WHAT THE CHART SHOWS
--
-- Two runs of the identical loop over the identical cases with the identical
-- seed, differing only in which judge selected the winner. Plot `generation` on
-- x and both scores on y, split by `judge`:
--
--   judge = 'honest'    honest_score climbs.        The agent gets better.
--   judge = 'gameable'  gameable_score is pinned near 1.0 from generation 0,
--                       so the coach sees almost nothing to fix, and
--                       honest_score stays flat. The scoreboard says "solved"
--                       while the business has not moved.
--
-- Every row carries BOTH scores because `score_case` always runs both judges.
-- That is what makes the divergence visible in one query: you can read the
-- honest score OF a gameable-optimised run, which is the number a team
-- optimising against the wrong judge would never have looked at.
--
-- TWO FILTERS THAT MATTER
--
--   NOT held_out    the held-out set is scored once, after the loop, and must
--                   never appear in an optimisation curve. Filtering on the
--                   row flag (rather than on which labels were holdout runs)
--                   means a mislabelled run still cannot contaminate the chart.
--   dry_run         stub-coach/stub-agent plumbing runs are excluded by
--                   default. Flip @include_dry_runs to TRUE to chart them.
--
-- If a (judge, generation) was ever run more than once, the most recent run by
-- start time wins, so re-running the loop refreshes the curve instead of
-- averaging two different agents together.

DECLARE include_dry_runs BOOL DEFAULT FALSE;

WITH scored AS (
  SELECT
    judge,
    generation,
    case_id,
    honest_passed,
    gameable_passed,
    run_label,
    run_started_at,
    -- One row per (judge, generation, case): keep the newest run of it.
    ROW_NUMBER() OVER (
      PARTITION BY judge, generation, case_id
      ORDER BY run_started_at DESC, run_label DESC
    ) AS recency
  FROM `quote_runner.eval_results`
  WHERE generation IS NOT NULL          -- GEPA generations only; naive/oracle have none
    AND NOT held_out                    -- the curve is a DEV curve, always
    AND (include_dry_runs OR NOT COALESCE(dry_run, FALSE))
)

SELECT
  judge,                                                    -- which judge selected the winner
  generation,
  COUNT(*)                                        AS n_cases,
  ROUND(AVG(CAST(honest_passed   AS INT64)), 4)   AS honest_score,
  ROUND(AVG(CAST(gameable_passed AS INT64)), 4)   AS gameable_score,
  -- The gap is the story: how much the gameable scoreboard overstates the agent.
  ROUND(
    AVG(CAST(gameable_passed AS INT64)) - AVG(CAST(honest_passed AS INT64)), 4
  )                                               AS overstatement,
  MAX(run_started_at)                             AS run_started_at
FROM scored
WHERE recency = 1
GROUP BY judge, generation
ORDER BY judge, generation;

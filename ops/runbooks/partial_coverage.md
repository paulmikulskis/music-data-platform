# Partial coverage

Read the receipt's successful target count, total and `min_target_coverage`.
The denominator is the run's eligible frozen targets. Non-due targets are excluded. A stale target counts as failed,
even when some of its pages landed.

The default floor is 90% for ten or more targets and 100% for smaller sets.
For example, 58 of 59 targets clears the floor. The run stays partial and the cycle
can close. Fewer successes than the floor fails the run and its invoke model.
A source declared `blocks_cycle=False` (Apple song durations) fails its run and keeps its alerts,
but its invoke model returns the failed receipt, so the cycle still closes.
`allow_partial` still admits declared time-budget work. It cannot override a target floor.
A target succeeds after it finishes without delivery errors.
Expected exclusions do not make a target fail.
Each receipt shows that batch's row coverage and error share: errors / (written + errors).
The rejected count includes errors and expected exclusions.
Expected exclusions keep their counts and reasons but never lower row health.
These include playlist non-track items, KEXP air breaks, untracked MusicBrainz URLs.
For example, one Apple track and nine music videos write two rows and record nine exclusions.
Row acceptance is 100%. A KEXP page of air breaks has no eligible rows and passes.
A batch with only error rejections has 0% acceptance and fails its row floor.
The run combines row counts across batches; more than 50% error rejections fails the run,
even when every target succeeds. The declaration can set a stricter `min_row_coverage`.
Billboard requires 90% accepted rows: one malformed position passes, but half or all rejected fails.
Batches with no written rows or error rejections have no row percentage.
For example, ten targets each delivering one valid row and nine error rejections
have 100% target coverage and 10% row acceptance. That run fails.
A single rejected target among nine delivered targets stays partial above its target floor.
Batch size never changes the run decision. Rejected loads and broken accounting fail.
Staff and operators see receipts on the function page even when no output preview is available.
Open the function page, expand its receipts, then follow **Check coverage**.

Inspect target warnings before retrying. Repair the cause, then Retry the full cycle.
Canaries run after deploy and rebuilds. They fetch and validate a sample in memory.
They create no runs, cycles, dumps or cursors. No work due reports `not_due`.
A paused function, no active targets or tenants, a source without `canary=True`, or stateful execution reports `skipped`.
Probes check the function's enabled state at admission and again before dispatch.
A busy, blocked or backed-off host also reports `skipped`; probes use shared host admission.
Neither opens an alert. A failed or timed-out sample opens `source_canary_failed`.
Open the function link in the report, inspect the audit's error class, then rerun
`uv run --project functions python ops/fly/resilience-checks.py`.

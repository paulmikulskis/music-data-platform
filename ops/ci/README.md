# Acceptance scripts

Run from the repository root with matching role URLs and service settings. Suites write
dated evidence; integration skips or offline-only results cannot certify a full suite.

| Entry point | Coverage | Evidence |
|---|---|---|
| `ops/ci/accept-platform.sh --local` | Preflight, dbt lint, control typecheck, Python tests/lint, DuckDB build and lifecycle | platform |
| `ops/ci/accept-platform.sh --deployed` | Deployed data flow and lifecycle wrapper | deployed report |
| `ops/ci/accept-control.sh` | Control/data APIs, forms, contracts and required integration tests | control |
| `ops/ci/accept-adversarial.sh` | Adversarial catalog and declared-key/receipt audit | adversarial |

Local preflight tolerates MISSING deployment inputs, never FAIL probes.
`--only-lifecycle` explicitly excludes other legs.



## Lifecycle

`ops/ci/lifecycle.sh --target pg_local --case all` runs ten cases: scheduled build, full retry,
batch resume, frozen membership/late commit, supersession, closed rerun/tenant scope,
older replay/refusal, service interruption, runner switch and timed DuckDB loop.
`--case c` isolates batch resume. Fixture plans inject vendor responses through authenticated
routes enabled only with MDP_FIXTURE_MODE=1; they do not replace landing or cycle algorithms.
Resets require explicit disposable-stack ownership and matching endpoints/identity; read
[operations](../CLAUDE.md#acceptance) and [harness rules](lifecycle/README.md) before claiming.


## Adversarial and graph checks

```sh
bash ops/ci/lint-dbt.sh
ops/ci/adversarial/run-all.sh --offline-only
ops/ci/adversarial/run-all.sh
ops/ci/adversarial/circular-ref/run.sh
ops/ci/accept-adversarial.sh
```

`adversarial/catalog.json` contains 41 cases. Each has fixture input, literal expected
detector output, a named invariant and an existing runbook. dbt ls constructs the graph
and detects cycles; lint also enforces materialization, dependencies, cadence/scope and
shared-tenant contracts. MDP_LINT_DBT_ROOT/MDP_LINT_RULES_ONLY support isolated fixture projects.

Each full run gets a fresh evidence directory. Shared lifecycle executions can be reused
within that suite only; every row independently matches its expected detector assertions.
The final audit checks declared keys, committed receipt cardinality/generation and orphan or
unfinished loads. Rejected outputs retain their dead letters without landed rows.
The pull-request workflow provisions Compose with an explicit heap image and uploads evidence
even on failure. It covers the runtime matrix, not pg_lake storage acceptance.

`python3 ops/ci/review_gate.py` checks every served mart (`meta.grain`): each literal `_source_keys` entry
must name a raw writer upstream in the manifest, and a mart with upstream writers must carry keys.
Use row keys or carried arrays where available; document a literal where no per-row key exists.
`python3 -m unittest discover -s ops/ci -p test_review_gate.py` proves rejection of an injected
unrelated key and missing annotations. This static check validates literal attribution, not the
contents of row-derived arrays; fixture rights counts and adapter parity cover those changes.


## Evidence size

Run `python3 ops/ci/evidence_size.py` before committing evidence.
It compares the merge base of `main` and `HEAD` with your working files.
It includes tracked edits and untracked files that Git does not ignore.
To check committed files, run `python3 ops/ci/evidence_size.py --base main --head HEAD`.
CI uses the pull request's merge base and head, or the push's before and after commits.
A missing Git ref fails the check. Run `git fetch --unshallow` in a shallow clone, then retry.

A new or changed file under `ops/evidence/` may hold at most 1,000,000 bytes (1 MB).
The range may add at most 5,000,000 bytes (5 MB).
Added bytes count each new file version at its full size, once per path and Git blob ID.
Replacements count even when their size stays the same. A rename counts as a new path.
Every commit in the range is checked, including files added and later deleted.
Deleted or smaller files do not cancel another file's growth.
These are uncompressed file sizes, not Git's compressed transfer sizes.
The check does not rewrite or reduce existing Git history.

[evidence-size-allowlist.json](evidence-size-allowlist.json) records existing large files and their byte sizes.
Each size must match its file at the pinned baseline commit.
An existing file may stay that size or shrink. Any later growth above 1 MB fails.
Replacing an allowed file still counts toward the range's 5 MB budget.
A deleted file loses its allowance. Do not add an allowance for a new artifact.
Crop screenshots, shorten logs, or keep large outputs in scratch outside the repository.
Keep predictions in backtest scratch; see [the backtest guide](../backtest/README.md).
Screenshot uploads wait for an operator-owned bucket. Ask the owner to provide that bucket before uploading.

Run the pass and fail cases with `python3 -m unittest discover -s ops/ci -p test_evidence_size.py`.

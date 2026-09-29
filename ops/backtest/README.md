# Song backtest

```sh
uv run --project functions python ops/backtest/run.py capture
uv run --project functions python ops/backtest/run.py replay
uv run --project functions python ops/backtest/run.py label
uv run --project functions python ops/backtest/run.py score
uv run --project functions python ops/backtest/run.py report
```

This measures whether a flagged song later reaches a new chart market, enters a major
editorial list, or sustains faster streams. It rebuilds each closed daily global cycle.
A cycle is one collection run with a fixed list of readable data batches.
Open the evidence report for the current result.

The snapshot starts with an empty [choices.json](choices.json). Before the first capture, record each method using a commit reachable in your checkout and its selection date.
Each entry has a full commit SHA and the actual last day used to choose its rules:

```json
{"example": {"sha": "<full-commit-sha>", "chosen_on": "2026-09-26"}}
```

Commit that file so git history records the choice independently of the private capture.
The harness reads the record from `HEAD`; an uncommitted edit does not change it.
Use a new method name for changed rules. The pinned SHA includes its dbt models and seeds.
`state.json` caches this record. A missing date or a different cached date or SHA stops scoring
and reporting. A retained `report.json` must also agree before it can produce Markdown.
Run `weekly.sh` with a fresh scratch directory after committing a changed record.

Capture and replay default to the committed methods on a fresh run.
`--method NAME=<commit>` selects a recorded method.
The optional `--chosen-on NAME=YYYY-MM-DD` must match its committed date.
Scoring defaults to the latest selection date across the compared methods.
`--chosen-through` can move this shared cutoff later, never earlier.
Only days after that cutoff contribute to performance, coverage or arrivals.
The CSV inventory in scratch keeps all rebuilt days for audit. Open `report.json` for each method's date.


## Run locally

Install Docker and uv, and provide the `secret_store` adapter. Start Docker.
Run these commands from the repository root.
Capture needs the existing read-only warehouse proxy at `127.0.0.1:15471`.
On a laptop, use the same local proxy port and the project's secret store access.
The harness does not create a production proxy or change its settings.
Follow [the connection guide](../../docs/operating.md) if the proxy is unavailable.

The harness asks Docker for a free loopback port and checks its container label before writing.
It never accepts an arbitrary write URL.
One private Postgres container holds the copied inputs and `backtest` tables.
Each method gets its own `bt_<name>` schema.
The schema is emptied before each day, so yesterday's incremental state cannot enter a build.
Run `report --cleanup` when finished to remove the container and private working rows.

`MDP_BACKTEST_SCRATCH` defaults to `/tmp/mdp-backtest`.
Use a path outside every repository. Its files are private to the local user.
The capture contains raw text needed by identity rules. Never commit that directory.
Delete it after the report; do not keep raw text beyond its source's retention period.
Only the report summaries leave the scratch directory.
Predictions and CSV inventories stay in its `results/` folder for every command.
No titles, credits, playlist descriptions or copied source text reach the CSVs.
Open `$MDP_BACKTEST_SCRATCH/results/` to inspect them before cleanup.


## Capture

Set `MDP_BACKTEST_READ_URL` to the `service_read` connection URL to use one credential.
When it is unset, `capture` reads only `MDP_SERVICE_READ_URL` from secret store
(`music-data-platform`, `prd`). An empty or invalid value does not use the fallback.
Both paths force the connection through `127.0.0.1:15471`.
It verifies the `service_read` login. It never prints the URL.
Production sessions use read-only transactions, a one-second lock timeout and at most
15 seconds per statement. Each cycle has its own consistent read transaction.
Every raw content query filters through that cycle's manifest before copying.
Cycle mirrors and frozen target revisions are bounded to the same global close.
Method seeds come from the pinned git ref, not today's warehouse tables.

Capture reads recent scheduled runner bindings from `raw.cycle_attempts`, joined to `raw.cycles`,
through the same read-only connection. These are the declared warehouse mirrors.
Each runner's latest start sets its next hourly, daily or weekly window.
Capture pauses two minutes before that start. It waits at least ten minutes after an hourly
start and thirty after a daily or weekly start, with ten minutes beyond the recorded close.
Close precedes transforms, so that extra time protects the build too.
These windows estimate scheduled work; they do not claim to observe every live runner lock.
Missing, unreadable or stale starts stop capture with a recovery step.
Each wait stops after one hour, even if runner windows keep moving.
Check runner timing in [the operating guide](../../docs/operating.md), then retry capture.
A separate short read refreshes starts while a cycle's inputs stay in one consistent transaction.
Statement deadlines shorten before a window. An interrupted day is recaptured on retry.
Completed days have hashes and can be reused. Run `capture` again to resume.


## Replay

Add both method commits and dates to `choices.json`, then commit it before comparing:

```sh
uv run --project functions python ops/backtest/run.py capture \
  --method movement_v2=<commit> --method cluster=<other-commit>
uv run --project functions python ops/backtest/run.py replay \
  --method movement_v2=<commit> --method cluster=<other-commit>
```

A method is the dbt project, seeds and locked dependencies at that git ref.
Only schema routing changes in the private copy.
The production cycle filters stay active through the `pg_local` target and the recorded run binding.
Collection, export, close and enrichment invocations are excluded.
All SQL ancestors are rebuilt locally from captured raw data, including identity ancestors
normally owned by another cadence. Nothing reads production's transformed history.
Missing retained reference generations stop that cycle's build; the harness never substitutes today's identity.
The harness records the missing generation and tries the remaining cycles.
Comparisons use only days whose last cycle succeeds for every method.
Open `failures.csv` before comparing scores.

The output grain is `(cycle, method, song key at that cycle, list, movement split)`.
Lists are movers, early signals by family, arrivals and fixed baselines.
Splits are `new_entries`, `established_entries`, `catalog_entries` and `unplaced`.
Arrivals have a rank and no score. Each row keeps all movement components.
The baselines rank the observable pool by a fixed random seed, followers, chart count,
playlist adds (then entered reach for songs with no adds), Shazam spread, or stream gain.
Baselines keep their top 50 per split, the largest tested cutoff.
Read `results/predictions.csv` under scratch to compare the original feature values.


## Labels

Labels become true after the prediction day, within 7, 14 or 28 days:

| Label | What counts |
| --- | --- |
| Shazam country or city | First observed entry in that place, with an earlier complete read of that exact chart proving absence |
| Chart market | Full-list add in a market on a tracked Apple Top 100 or Spotify Top 50 list; visible-head entries do not count |
| Major editorial list | A full-list add to tier 1 in the movement reach-tier seed; a head entry is insufficient |
| Stream surge | Seven consecutive baseline days, then three consecutive days at least 50% higher; zero baselines and gaps stay unknown |
| Artist chart entry | The first tracked chart entry of a linked primary artist |
| Billboard debut | First keyed group week with a provider week count of one, complete current and preceding Hot 100 charts, and no earlier candidate for the group. A negative needs complete Saturday reads and no unkeyed entry whose folded title matches any group member. A keyed entry with an unknown debut keeps its own group pending. Open `events.csv` for group IDs. |

“First” means first in captured tracked history. It does not prove a lifetime debut.
An initial chart snapshot is presence, not an entry. Every Saturday issue in a Billboard
negative's horizon must be a complete read. Unmatched and ambiguous entries block only groups
with a possible title match. Negative checks remove featured credits and version suffixes
in parentheses or brackets. For example, `Example Song (feat. Guest)` blocks `Example Song`.
`feat.`, `ft.`, `featuring`, `with` and `x` credits also count as possible matches.
This broad match only keeps negatives pending; it never proves a positive.
An unkeyed song outside the group does not block its negative.
A keyed entry whose debut is unknown still blocks its own group.
Positives count as soon as the existing debut rule proves them.
Negative checks use every member of the movement group frozen at the prediction cycle.
If that group later splits, a blocker for either part still keeps the original prediction pending.
Missing frozen membership or a member missing from the outcome reference also keeps it pending.
Positive outcomes keep the existing matcher. Open `labels.json` for the outcome reference.
Open the report for the keyed share and pending or matured counts.
Playlist absence needs complete reads of the same list, variant and stream.
Shazam absence needs all positions from one read: 200 for a country Top 200 or 50 for a city Top 50.
Missing or rejected rows cannot prove absence. Discovery charts lack a stored extent proof and stay pending.
An artist with any chart baseline on the entry date cannot receive a first-entry label that day.
Editorial recall and lead time exclude flags issued while the song is already on that outcome's list.
The stream event date is the third raised day, when the condition becomes known.
The same threshold is fixed for every method. Open `events.csv` to inspect labels.

The first replayed method defines the shared outcome reference at the latest captured cycle.
Later `mart_song_aliases` mappings join outcomes only.
They never change a prediction key, component, rank or score.
If later aliases split an old key into several keys, its outcome stays unmatched.
Read `labels.json` for the reference method and cycle.


## Read the report

`report` writes Markdown and JSON to `ops/evidence/backtest/`.
Use `--output <directory>` to choose where `labels.json`, `report.json` and `report.md` go.
Capture, replay and label keep every CSV in scratch, even when `--output` points into the repository.
The Markdown gives the short answer. The JSON carries every metric and denominator.

Each metric has its sample size and a 95% interval from resampling whole UTC Monday weeks.
An interval needs at least two observed weeks. An absent interval is never printed as zero.
Precision uses top 10, 25 and 50, first by day and then pooled by week.
One song contributes at most once per week to a list's pooled precision.
Its first flag wins, so repeated flags cannot inflate the sample.
Recall asks whether each event has a flag at least 1, 3 or 7 days earlier.
Lead time uses the earliest flag within the horizon.
Coverage includes events outside the predicted list and asks whether their song appeared in
two families at an earlier evaluation day. It measures the observable catalog, not forecast quality.

A negative label needs a complete read of every relevant tracked target on every day of its horizon.
The required set includes unread frozen chart and playlist targets, with playlist variants kept separate.
Stream labels need each of the song's known Spotify counters, plus the baseline days before a possible surge.
Another song's read cannot complete that requirement. Missing or partial reads leave the outcome pending.
Untracked markets and unknown identities remain limits of observation.
All methods must finish the same cycles before scoring.
One day uses its last closed daily cycle; repeated same-day builds do not enlarge the daily sample.
Family coverage uses all members of the movement group at the prediction cycle.
Playlist sizes count each platform/list pair once per group and day, using its largest observed
follower count. Chart sizes count each chart ID once per group and day.
Two members on the same chart therefore contribute one chart, not two.
Stream presence uses any member's observed counter in `mart_song_day`. The report compares representative-only and group counts on the latest capture.
The JSON includes paired differences when more than one method is present.
Their sample size counts unique song/week pairs across both methods within shared weeks.
Under 14 distinct history days, only coverage and arrivals are reported.
There is no calibration score because movement scores are not probabilities.
Capture later cycles to enlarge the evaluation set.


## Weekly report

```sh
MDP_BACKTEST_SCRATCH=/tmp/mdp-backtest-weekly bash ops/backtest/weekly.sh /tmp/backtest-report

# Later runs reuse the pinned method and its selection date:
MDP_BACKTEST_SCRATCH=/tmp/mdp-backtest-weekly bash ops/backtest/weekly.sh /tmp/backtest-report
```

The orchestrator runs this command on Mondays. The script installs no schedule.
Use the actual selection date on the first run. To measure changed rules, choose a new method name.
The script captures, replays, labels and scores. It writes `report.md`, `report.json` and
`labels.json` to the directory argument. Predictions and CSV inventories stay in private scratch.
It attempts to remove the container, its volume, copied inputs and temporary reports on exit.
Cleanup first checks container ownership, its loopback port and a working database connection.
A stopped or unreachable container can leave private inputs and temporary reports behind.
After successful cleanup, private `state.json` retains only method choices and an empty cycle list.
Follow [recovery](#recover) if cleanup fails.
Open the output `report.md` after the command finishes.

Precision can first become reportable around 2026-10-07, with at least 14 captured history days
and mature outcomes after each method's selection date. That date is an estimate, not a promise
that every label has enough evidence. Intervals need two observed UTC weeks.
The first Monday run should show coverage and arrivals, with precision withheld.
It should also show the Hot 100 keyed share, pending or matured labels, and the group-family counts.
A title collision says “cannot mature while entries stay unkeyed”; waiting alone cannot fix it.
Open `report.json` to inspect the counts and methods before comparing runs.


## Rights

Replaying fixed, human-written rules and reporting how they did is measurement.
Searching weights or thresholds to maximize a backtest score is fitting.
Fitting makes the work a derivative consumer under `learning_gate`.
This harness never searches weights, thresholds, seeds or methods for a winning score.
Do not use it to train a model, tune rules on the reported days, claim causation, predict
commercial success, or publish restricted source data.
For fitting, first follow [the learning boundary](../../docs/architecture.md#rights-annotation-and-learning_gate).


## Recover

If capture stops, check Docker and the existing proxy, then rerun the same command.
If replay stops, read the private `<cycle>-run.log` under the scratch `methods/<commit>/` directory.
Do not copy that log into git: SQL or a driver error can include private values.
If a method needs another raw input, rerun capture with both method options, then replay.
If reference retention removed a required generation, ask the owner for its retained dumps.
After the owner restores them, use a fresh scratch directory to recapture and replay.
Do not read current marts as a substitute.
If a method name already pins another commit, choose a new name.
If the scratch container is gone, use a fresh scratch directory and run capture again.

If weekly cleanup fails, read `container`, `token` and `port` in the private scratch `state.json`.
Check that the container's `mdp.backtest` label matches the saved token:

```sh
docker inspect --format '{{ index .Config.Labels "mdp.backtest" }}' <container>
```

For a matching stopped container, run `docker start <container>`.
Wait until `docker exec <container> pg_isready -U postgres` succeeds, then rerun the same weekly command.
The harness checks the saved loopback port again before using that container.
If the container or port cannot be recovered, confirm the ownership label before running
`docker rm -f -v <container>`. Delete only that run's private scratch directory after checking its path.
If the container is already gone, remove its leftover private scratch directory.
Then set `MDP_BACKTEST_SCRATCH` to a fresh path and run `weekly.sh` again.

Run the tests with `bash ops/ready.sh`.
The dbt identity-leak test uses its own disposable Docker container and removes it on exit.
To finish a live measurement, run `report --cleanup` and open the evidence report.

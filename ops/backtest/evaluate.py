"""Outcomes, fixed rolling-origin estimates and a short evidence page."""

import json
import os
from collections import defaultdict
from datetime import date, timedelta
from itertools import combinations
from pathlib import Path

from ops.backtest import metrics
from ops.backtest.errors import BacktestError
from ops.backtest.warehouse import export, method_choices, save

KINDS = (
    "shazam_country",
    "shazam_city",
    "chart_market",
    "tier1_editorial",
    "stream_surge",
    "artist_first_chart",
    "billboard_debut",
)


class ObservationCoverage:
    """A miss needs complete reads of every relevant target, including unread targets."""

    def __init__(self, targets, observations, billboard_blockers=()):
        self.targets = defaultdict(set)
        self.days = defaultdict(set)
        for row in targets:
            self.targets[(row["kind"], row["song_key"])].add(row["target"])
        for row in observations:
            if row["complete"]:
                self.days[(row["kind"], row["target"])].add(row["day"])
        self.blockers = defaultdict(set)
        for row in billboard_blockers:
            self.blockers[(row["song_key"], row["day"])].add(row["reason"])
        self.cache = {}

    def mature(self, song, kind, day, horizon):
        key = (song, kind)
        if key not in self.cache:
            kinds = (
                ("shazam_country", "shazam_city", "chart_market")
                if kind == "artist_first_chart"
                else (kind,)
            )
            required = [
                (label, target)
                for label in kinds
                for target in self.targets[(label, None)] | self.targets[(label, song)]
            ]
            self.cache[key] = (
                set.intersection(*(self.days[t] for t in required))
                if required
                else set()
            )
        if kind == "billboard_debut":
            # Published Hot 100 weeks end on Saturday. Every due issue must be complete.
            weeks = [
                day + timedelta(days=i)
                for i in range(1, horizon + 1)
                if (day + timedelta(days=i)).weekday() == 5
            ]
            return bool(weeks) and all(
                week in self.cache[key] and not self.blockers[(song, week)]
                for week in weeks
            )
        # A surge ending on D+1 needs rates from D-8: seven baseline days and three raised days.
        first = -8 if kind == "stream_surge" else 1
        return all(
            day + timedelta(days=i) in self.cache[key]
            for i in range(first, horizon + 1)
        )


def outcome_eligible(row, event, editorial_presence):
    return (
        event["kind"] != "tier1_editorial"
        or (row["song"], row["day"], event["dimension"]) not in editorial_presence
    )


def reference(state):
    method = next(iter(state["methods"]))
    cycle = max(
        state["cycles"], key=lambda c: (c["opened_at"], c["close_no"] or 0, c["id"])
    )
    return {"cycle": cycle["id"], "method": method}


def daily_cycles(state):
    chosen = {}
    for cycle in sorted(
        state["cycles"], key=lambda c: (c["opened_at"], c["close_no"] or 0, c["id"])
    ):
        chosen[cycle["opened_at"][:10]] = cycle["id"]
    return list(chosen.values())


def billboard_summary(conn, params):
    week = conn.execute(
        "SELECT day,entries,keyed FROM backtest.billboard_weeks "
        "WHERE cycle_id=%(cycle)s AND method=%(method)s ORDER BY day DESC LIMIT 1",
        params,
    ).fetchone()
    events = conn.execute(
        "SELECT count(*) AS n FROM backtest.events WHERE kind='billboard_debut'"
    ).fetchone()["n"]
    blocked = conn.execute(
        "SELECT count(DISTINCT song_key) AS n FROM backtest.billboard_blockers "
        "WHERE cycle_id=%(cycle)s AND method=%(method)s AND reason='unkeyed_title'",
        params,
    ).fetchone()["n"]
    return {"week": week, "events": events, "blocked_groups": blocked}


def selection_cutoff(state, chosen_through):
    dates = [method.get("chosen_on") for method in method_choices(state).values()]
    if not dates or any(not value for value in dates):
        raise BacktestError("backtest_chosen_on_missing")
    cutoff = max(date.fromisoformat(value) for value in dates)
    if chosen_through:
        cutoff = max(cutoff, date.fromisoformat(chosen_through))
    return cutoff


def label(conn, state, output):
    params = reference(state)
    with conn.transaction():
        for statement in (Path(__file__).parent / "labels.sql").read_text().split(";"):
            if statement.strip():
                conn.execute(statement, params)
    export(conn, "events", output / "events.csv")
    save(
        output / "labels.json",
        {
            "reference": params,
            "kinds": KINDS,
            "horizons": metrics.HORIZONS,
            "billboard_debut": billboard_summary(conn, params),
            "first_entry_scope": "First in captured tracked history. This does not establish a career debut.",
        },
    )
    print("Outcomes are ready. Next: run score; it reads each method’s chosen_on date.")


def joined_rows(conn, state):
    # Ambiguous later splits cannot choose a convenient outcome. They stay unmatched.
    return conn.execute(
        """
        SELECT p.*,coalesce(k.canonical_key,p.song_key) AS song,
            k.song_key IS NOT NULL AND k.canonical_key IS NULL AS ambiguous
        FROM backtest.predictions p LEFT JOIN backtest.outcome_keys k
            USING(cycle_id,method,song_key)
        JOIN backtest.builds b USING(cycle_id,method,day)
        WHERE b.cycle_id=ANY(%s)
          AND (SELECT count(*) FROM backtest.builds x WHERE x.cycle_id=b.cycle_id)=%s
        ORDER BY p.day,p.method,p.list,p.movement_list,p.rank
    """,
        (daily_cycles(state), len(state["methods"])),
    ).fetchall()


def score(conn, state, chosen_through, output):
    cutoff = selection_cutoff(state, chosen_through)
    params = reference(state)
    builds = conn.execute(
        "SELECT * FROM backtest.builds ORDER BY day,method,close_no"
    ).fetchall()
    expected = {(c["id"], m) for c in state["cycles"] for m in state["methods"]}
    actual = {(b["cycle_id"], b["method"]) for b in builds}
    failures = conn.execute(
        "SELECT * FROM backtest.failures ORDER BY cycle_id,method"
    ).fetchall()
    failed = {(r["cycle_id"], r["method"]) for r in failures}
    if not expected <= actual | failed:
        raise ValueError(
            "Methods do not cover the same cycles. Run replay for each method before score."
        )
    days = sorted({date.fromisoformat(c["opened_at"][:10]) for c in state["cycles"]})
    paired_cycles = {
        c
        for c in daily_cycles(state)
        if all((c, m) in actual for m in state["methods"])
    }
    evaluation_days = sorted(
        {
            b["day"]
            for b in builds
            if b["cycle_id"] in paired_cycles and b["day"] > cutoff
        }
    )
    young = len(days) < 14
    events = [
        dict(r, song=r["song_key"])
        for r in conn.execute("SELECT * FROM backtest.events")
    ]
    observation_coverage = ObservationCoverage(
        conn.execute(
            "SELECT kind,target,song_key FROM backtest.targets WHERE cycle_id=%(cycle)s AND method=%(method)s",
            params,
        ),
        conn.execute(
            "SELECT kind,target,day,complete FROM backtest.observations WHERE cycle_id=%(cycle)s AND method=%(method)s",
            params,
        ),
        conn.execute(
            "SELECT song_key,day,reason FROM backtest.billboard_blockers "
            "WHERE cycle_id=%(cycle)s AND method=%(method)s",
            params,
        ),
    )
    rows = [r for r in joined_rows(conn, state) if r["day"] > cutoff]
    by_song = defaultdict(list)
    for event in events:
        by_song[event["song"]].append(event)
    # Artist labels join the latest aliases' artist keys. Artist text is never a key.
    artists = defaultdict(set)
    for row in conn.execute(
        "SELECT song_key,artist_key FROM backtest.artists WHERE cycle_id=%(cycle)s AND method=%(method)s",
        params,
    ):
        artists[row["song_key"]].add(row["artist_key"])
    artist_events = defaultdict(list)
    for event in events:
        if event["kind"] == "artist_first_chart":
            artist_events[event["dimension"]].append(event)
    editorial_presence = set()
    for row in conn.execute(
        "SELECT song_key,day,dimension FROM backtest.facts WHERE cycle_id=%(cycle)s "
        "AND method=%(method)s AND kind='tier1_editorial' AND NOT candidate",
        params,
    ):
        editorial_presence.add((row["song_key"], row["day"], row["dimension"]))
    billboard_groups = {
        row["song_key"]: row["cluster_key"]
        for row in conn.execute(
            "SELECT song_key,cluster_key FROM backtest.billboard_members WHERE cycle_id=%(cycle)s AND method=%(method)s",
            params,
        )
    }
    # A later split must not discard members of the group we actually flagged.
    frozen_members = defaultdict(set)
    for member in conn.execute("SELECT * FROM backtest.billboard_members"):
        frozen_members[
            (member["cycle_id"], member["method"], member["cluster_key"])
        ].add(member["song_key"])
    grouped = defaultdict(list)
    for row in rows:
        grouped[(row["method"], row["list"], row["movement_list"])].append(row)
    results = []
    paired = []
    label_rows = {}
    billboard_labels = {}
    for horizon in metrics.HORIZONS:
        for kind in KINDS:
            for group, original_predictions in sorted(grouped.items()):
                predictions = original_predictions
                if kind == "billboard_debut":
                    predictions = [
                        dict(row, song=billboard_groups.get(row["song"], row["song"]))
                        for row in original_predictions
                    ]
                labelled = []
                for row in predictions:
                    end = row["day"] + timedelta(days=horizon)
                    mature = observation_coverage.mature(
                        row["song"], kind, row["day"], horizon
                    )
                    if kind == "artist_first_chart" and not artists[row["song"]]:
                        mature = False
                    candidates = by_song[row["song"]]
                    if kind == "artist_first_chart":
                        candidates = [
                            e for a in artists[row["song"]] for e in artist_events[a]
                        ]
                    hit = not row["ambiguous"] and any(
                        e["kind"] == kind
                        and row["day"] < e["day"] <= end
                        and outcome_eligible(row, e, editorial_presence)
                        for e in candidates
                    )
                    if kind == "billboard_debut":
                        members = frozen_members[
                            (row["cycle_id"], row["method"], row["song_key"])
                        ]
                        # Missing frozen membership, or a missing member in the
                        # outcome reference, cannot establish absence.
                        groups = {
                            billboard_groups[m]
                            for m in members
                            if m in billboard_groups
                        }
                        membership_known = bool(members) and all(
                            m in billboard_groups for m in members
                        )
                        mature = hit or (
                            membership_known
                            and all(
                                observation_coverage.mature(
                                    g, kind, row["day"], horizon
                                )
                                for g in groups
                            )
                        )
                    labelled.append(
                        {**row, "hit": hit, "mature": mature and not row["ambiguous"]}
                    )
                    if kind == "billboard_debut":
                        blocked = any(
                            "unkeyed_title"
                            in observation_coverage.blockers[
                                (g, row["day"] + timedelta(days=i))
                            ]
                            for g in groups
                            for i in range(1, horizon + 1)
                        )
                        billboard_labels[
                            (row["method"], row["song"], row["day"], horizon)
                        ] = {
                            "mature": hit or (mature and not row["ambiguous"]),
                            "blocked": blocked and not hit,
                        }
                label_rows[(group, horizon, kind)] = labelled
                if young:
                    continue
                for k in metrics.KS:
                    result = {
                        "method": group[0],
                        "list": group[1],
                        "split": group[2],
                        "kind": kind,
                        "horizon": horizon,
                        "k": k,
                        "precision": metrics.precision(labelled, k),
                        "daily": metrics.daily_precision(labelled, k),
                        "weekly": metrics.weekly_precision(labelled, k),
                    }
                    # Event denominators include songs outside the selected list and outside the pool.
                    # Require an evaluation origin at least horizon days before each event.
                    eligible_events = [
                        e
                        for e in events
                        if e["kind"] == kind
                        and evaluation_days
                        and e["day"] >= evaluation_days[0] + timedelta(days=horizon)
                    ]
                    flags = [
                        r for r in predictions if r["rank"] <= k and not r["ambiguous"]
                    ]
                    if kind == "artist_first_chart":
                        flags = [
                            {**r, "song": e["song"]}
                            for r in flags
                            for e in eligible_events
                            if e["dimension"] in artists[r["song"]]
                        ]
                    result["leads"] = {
                        str(lead): metrics.event_metrics(
                            eligible_events,
                            flags,
                            horizon,
                            lead,
                            eligible=lambda flag, event: outcome_eligible(
                                flag, event, editorial_presence
                            ),
                        )
                        for lead in metrics.LEADS
                    }
                    results.append(result)
            if not young:
                for left, right in combinations(sorted(grouped), 2):
                    if left[1:] != right[1:] or left[0] == right[0]:
                        continue
                    for k in metrics.KS:
                        paired.append(
                            {
                                "left": left[0],
                                "right": right[0],
                                "list": left[1],
                                "split": left[2],
                                "horizon": horizon,
                                "kind": kind,
                                "k": k,
                                **metrics.paired_difference(
                                    label_rows[(left, horizon, kind)],
                                    label_rows[(right, horizon, kind)],
                                    k,
                                ),
                            }
                        )
    pools = defaultdict(lambda: defaultdict(list))
    for row in conn.execute(
        """
        SELECT p.method,p.day,p.families,coalesce(k.canonical_key,p.song_key) AS song
        FROM backtest.pool p LEFT JOIN backtest.outcome_keys k USING(cycle_id,method,song_key)
        WHERE p.day>%s AND (k.song_key IS NULL OR k.canonical_key IS NOT NULL)
          AND p.cycle_id=ANY(%s)
    """,
        (cutoff, list(paired_cycles)),
    ):
        pools[row["method"]][row["song"]].append((row["day"], row["families"]))
    cover = []
    for method in state["methods"]:
        for horizon in metrics.HORIZONS:
            for kind in KINDS:
                eligible = [
                    e
                    for e in events
                    if e["kind"] == kind
                    and evaluation_days
                    and e["day"] > evaluation_days[0]
                ]
                pool = pools[method]
                if kind == "billboard_debut":
                    pool = defaultdict(list)
                    for song, observations in pools[method].items():
                        pool[billboard_groups.get(song, song)].extend(observations)
                if kind == "artist_first_chart":
                    pool = {
                        e["song"]: [
                            observation
                            for song, observations in pools[method].items()
                            if e["dimension"] in artists[song]
                            for observation in observations
                        ]
                        for e in eligible
                    }
                cover.append(
                    {
                        "method": method,
                        "kind": kind,
                        "horizon": horizon,
                        **metrics.coverage(eligible, pool, horizon),
                    }
                )
    arrivals = []
    for group, predictions in sorted(grouped.items()):
        if group[1] != "arrivals":
            continue
        unique = metrics.unique_song_weeks(predictions)
        blocks = defaultdict(list)
        for row in unique:
            blocks[metrics.week(row["day"])].append(float(row["families"] >= 2))
        arrivals.append(
            {
                "method": group[0],
                "split": group[2],
                "unique_song_weeks": len(unique),
                "two_family_share": metrics.estimate(blocks),
            }
        )
    summary = {
        "days": [str(d) for d in days],
        "cycles": len(state["cycles"]),
        "methods": state["methods"],
        "chosen_through": str(cutoff),
        "evaluation_days": [str(d) for d in evaluation_days],
        "history_days": len(days),
        "young_history": young,
        "coverage": cover,
        "arrivals": arrivals,
        "metrics": results,
        "paired": paired,
        "label_reference": params,
        "baselines": [
            "random (fixed seed)",
            "current followers",
            "current chart count",
            "playlists alone",
            "Shazam alone",
            "streams alone",
        ],
        "ambiguous_predictions": sum(r["ambiguous"] for r in rows),
        "replayed_cycles": len(actual),
        "failures": failures,
        "billboard_debut": billboard_summary(conn, params),
        "billboard_maturity": {
            "unit": "method/song/day/horizon; repeated lists count once",
            "mature": sum(r["mature"] for r in billboard_labels.values()),
            "pending": sum(not r["mature"] for r in billboard_labels.values()),
            "unkeyed_title": sum(r["blocked"] for r in billboard_labels.values()),
        },
        "family_counts": conn.execute(
            "SELECT cycle_id,method,count(*) AS groups,"
            "count(*) FILTER(WHERE before_families>=2) AS before_two_families,"
            "count(*) FILTER(WHERE after_families>=2) AS after_two_families,"
            "count(*) FILTER(WHERE before_followers IS DISTINCT FROM after_followers) AS follower_sizes_changed,"
            "count(*) FILTER(WHERE before_charts IS DISTINCT FROM after_charts) AS chart_sizes_changed "
            "FROM backtest.family_audit WHERE cycle_id=%s GROUP BY cycle_id,method ORDER BY method",
            (params["cycle"],),
        ).fetchall(),
        "interval": "95% percentile interval; 1000 resamples of whole UTC Monday weeks; fewer than two weeks leaves the interval unavailable.",
    }
    save(output / "report.json", summary)
    print(
        "Scores are ready. Next: uv run --project functions python ops/backtest/run.py report."
    )


def number(result):
    if result["value"] is None:
        return f"unavailable (n={result['n']}; interval unavailable)"
    interval = result["interval"]
    bounds = (
        f"{interval[0]:.3f}–{interval[1]:.3f}"
        if interval
        else "unavailable; collect another week"
    )
    return f"{result['value']:.3f} (n={result['n']}; 95% interval {bounds})"


def report(state, output):
    cutoff = selection_cutoff(state, None)
    result = json.loads((output / "report.json").read_text())
    selection_cutoff({"methods": result["methods"]}, None)
    if (
        result["methods"] != state["methods"]
        or date.fromisoformat(result["chosen_through"]) < cutoff
    ):
        raise BacktestError("backtest_choice_mismatch")
    guide = os.path.relpath(Path(__file__).with_name("README.md"), output)
    days = result["days"]
    lines = [
        "# Song backtest",
        "",
        "We cannot yet say how often a flagged song breaks out.",
    ]
    if result["young_history"]:
        lines += [
            f"The capture covers {result['history_days']} distinct days. Fewer than 14 days means coverage and arrivals only.",
            "Precision, recall and lead-time scores are withheld. Capture more daily cycles before judging a method.",
        ]
    lines += [
        "",
        f"Capture: {days[0]} through {days[-1]}, across {result['cycles']} closed daily global cycles.",
        f"The shared cutoff is {result['chosen_through']}, at least every method’s selection date. Scores use only later days.",
        "Evaluation days: "
        + (", ".join(result["evaluation_days"]) or "none; capture a later cycle")
        + ".",
        "Each day uses its last closed daily cycle. Every captured cycle is attempted; an unavailable cycle never falls back to an earlier build.",
        "",
        "## Methods",
        "",
    ]
    for name, method in result["methods"].items():
        lines.append(f"- `{name}`: `{method['sha']}`, chosen on {method['chosen_on']}.")
    if result["failures"]:
        refs = sorted({r["reference_id"] for r in result["failures"]})
        lines += [
            "",
            f"{len(result['failures'])} cycle/method builds cannot read retained reference data.",
            "Missing generation IDs: " + ", ".join(f"`{r}`" for r in refs) + ".",
            "Ask the owner to restore their retained dumps, then recapture and replay those cycles. Open the failures in [report.json](report.json).",
        ]
    lines += [
        "",
        "Baselines: " + ", ".join(result["baselines"]) + ".",
        "",
        "## Arrivals",
        "",
    ]
    if not result["arrivals"]:
        lines.append("No arrivals on evaluation days. Capture a later daily cycle.")
    for row in result["arrivals"]:
        lines.append(
            f"- {row['method']} / {row['split']}: {row['unique_song_weeks']} unique song/week arrivals. "
            f"Share seen in two families: {number(row['two_family_share'])}."
        )
    lines += [
        "",
        "## Coverage",
        "",
        "Coverage counts later label events whose song was seen in two families on an earlier evaluation day.",
        "A family counts only when the song has a playlist presence, a Shazam presence, or an observed stream counter.",
        "Zero-filled source-wide collection flags do not prove a song match.",
        "",
    ]
    if all(c["n"] == 0 for c in result["coverage"]):
        lines.append(
            "All label horizons have n=0 after the selection cutoff. Coverage and its interval are unavailable. Capture later cycles."
        )
    else:
        for row in result["coverage"]:
            lines.append(
                f"- {row['method']} / {row['kind']} / {row['horizon']} days: {number(row)}."
            )
    lines += ["", "## Group coverage", ""]
    for row in result["family_counts"]:
        lines.append(
            f"{row['method']}: {row['before_two_families']} groups have two families on the representative alone; "
            f"{row['after_two_families']} have two across all members, out of {row['groups']} groups. "
            f"Follower sizes change for {row['follower_sizes_changed']}; chart sizes change for {row['chart_sizes_changed']}. "
            "Open report.json for the cycle ID."
        )
    billboard = result["billboard_debut"]
    lines += ["", "## Hot 100", ""]
    if billboard["week"]:
        week = billboard["week"]
        lines.append(
            f"{week['keyed']} of 100 Hot 100 entries keyed this week "
            f"({week['day']}; {week['entries']} entries read). Open labels.json for counts."
        )
    else:
        lines.append(
            "Hot 100 coverage is unavailable. Replay a method with the Billboard bridge."
        )
    maturity = result["billboard_maturity"]
    lines.append(
        f"{billboard['events']} group events; {maturity['mature']} matured labels; {maturity['pending']} pending labels. "
        "Open report.json for the counting unit."
    )
    if billboard["blocked_groups"]:
        lines.append(
            f"{billboard['blocked_groups']} groups cannot mature while entries stay unkeyed in their matching weeks. "
            "Run capture and replay to review the Billboard title matches in a private copy."
        )
    lines.append(
        "Missing Saturday reads and a keyed entry with an unknown debut also keep its label pending. "
        "Open the harness guide for the maturity rules."
    )
    if result["metrics"]:
        lines += ["", "## Scores", ""]
        for row in [
            r
            for r in result["metrics"]
            if r["k"] == 25 and r["horizon"] == 7 and r["kind"] == "shazam_country"
        ]:
            lines.append(
                f"- {row['method']} / {row['list']} / {row['split']} / {row['kind']} / {row['horizon']} days / "
                f"top {row['k']}: precision {number(row['precision'])}."
            )
        lines += [
            "",
            "This short view shows seven-day Shazam-country outcomes at top 25. Every other score, daily precision, recall at each lead, lead-time percentile and paired difference is in [report.json](report.json).",
        ]
    lines += [
        "",
        "## Read the limits",
        "",
        result["interval"],
        "The report excludes days used to choose rules. Run replay separately to keep an inventory for audit.",
        "First entry means first in the captured tracked history. It does not establish a lifetime or career debut.",
        "A first collection is a baseline. Only a bounded absence followed by presence can start a chart label.",
        "A missing or incomplete target read leaves a negative outcome pending. Every relevant tracked target must be complete throughout the horizon.",
        "Artist labels use known primary artist IDs. Missing artist links reduce measured coverage.",
        "Later aliases join outcomes only. Ambiguous identity splits stay unmatched; inspect the JSON count.",
        "",
        "## A later showcase table",
        "",
        "A served outcome table needs a frozen pick ID, pick time, original song key, method revision and list rank.",
        "It also needs the outcome key, event date, horizon, observation status, rights annotations and an enforced dbt contract.",
        "Keep pending outcomes distinct from misses. Link each outcome to its captured source evidence.",
        "",
        "## Next step",
        "",
        f"Collect later daily cycles, then repeat the commands in [the harness guide]({guide}).",
        "",
    ]
    (output / "report.md").write_text("\n".join(lines))
    print(f"Report is ready. Next: open {output / 'report.md'}.")

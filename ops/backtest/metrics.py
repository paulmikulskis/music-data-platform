"""Fixed estimators. Weeks, not repeated daily rows, are bootstrap blocks."""

import hashlib
import math
import random
from collections import defaultdict
from datetime import timedelta
from statistics import mean

HORIZONS = (7, 14, 28)
KS = (10, 25, 50)
LEADS = (1, 3, 7)
SEED = "mdp-backtest-v1"


def week(day):
    return day - timedelta(days=day.weekday())


def random_order(day, keys):
    """Matches the SQL baseline, independent of input order and other methods."""
    return sorted(
        keys, key=lambda key: hashlib.md5(f"{SEED}:{day}{key}".encode()).hexdigest()
    )


def quantile(values, fraction):
    if not values:
        return None
    ordered = sorted(values)
    index = (len(ordered) - 1) * fraction
    low = math.floor(index)
    return ordered[low] + (ordered[math.ceil(index)] - ordered[low]) * (index - low)


def estimate(blocks, statistic=mean, iterations=1000):
    """Each value is one unique song/week, or one unique event for recall/lead."""
    values = [value for block in blocks.values() for value in block]
    result = {
        "value": statistic(values) if values else None,
        "n": len(values),
        "weeks": len(blocks),
        "interval": None,
        "interval_reason": None,
    }
    if len(blocks) < 2:
        result["interval_reason"] = (
            "At least two observed weeks are needed. Capture more daily cycles."
        )
        return result
    rng = random.Random(SEED)
    keys = sorted(blocks)
    samples = []
    for _ in range(iterations):
        sampled = [v for key in rng.choices(keys, k=len(keys)) for v in blocks[key]]
        samples.append(statistic(sampled))
    result["interval"] = [quantile(samples, 0.025), quantile(samples, 0.975)]
    return result


def unique_song_weeks(rows):
    """Earliest flag in a week wins; later repeated flags do not enlarge n."""
    unique = {}
    for row in sorted(rows, key=lambda r: (r["day"], r["rank"], r["song"])):
        unique.setdefault((row["song"], week(row["day"])), row)
    return list(unique.values())


def precision(rows, k):
    selected = unique_song_weeks([r for r in rows if r["rank"] <= k and r["mature"]])
    blocks = defaultdict(list)
    for row in selected:
        blocks[week(row["day"])].append(float(row["hit"]))
    return estimate(blocks)


def daily_precision(rows, k):
    days = defaultdict(list)
    for row in rows:
        if row["rank"] <= k and row["mature"]:
            days[row["day"]].append(row)
    return [
        {"day": str(day), **estimate({week(day): [float(r["hit"]) for r in values]})}
        for day, values in sorted(days.items())
    ]


def weekly_precision(rows, k):
    weeks = defaultdict(list)
    for row in rows:
        weeks[week(row["day"])].append(row)
    return [
        {"week": str(day), **precision(values, k)}
        for day, values in sorted(weeks.items())
    ]


def event_metrics(events, flags, horizon, lead, eligible=None):
    blocks = defaultdict(list)
    times = defaultdict(list)
    by_song = defaultdict(list)
    for row in flags:
        by_song[row["song"]].append(row)
    seen = set()
    for event in events:
        identity = (event["song"], event["kind"], event["dimension"], event["day"])
        if identity in seen:
            continue
        seen.add(identity)
        gaps = [
            (event["day"] - flag["day"]).days
            for flag in by_song[event["song"]]
            if lead <= (event["day"] - flag["day"]).days <= horizon
            and (eligible is None or eligible(flag, event))
        ]
        blocks[week(event["day"])].append(float(bool(gaps)))
        if gaps:
            times[week(event["day"])].append(max(gaps))
    result = {
        "recall": estimate(blocks),
        "lead_median": estimate(times, lambda x: quantile(x, 0.5)),
        "lead_p25": estimate(times, lambda x: quantile(x, 0.25)),
    }
    result["unique_song_weeks"] = len({(e["song"], week(e["day"])) for e in events})
    return result


def coverage(events, pools, horizon):
    blocks = defaultdict(list)
    for event in events:
        covered = any(
            0 < (event["day"] - day).days <= horizon and families >= 2
            for day, families in pools.get(event["song"], [])
        )
        blocks[week(event["day"])].append(float(covered))
    result = estimate(blocks)
    result["unique_song_weeks"] = len({(e["song"], week(e["day"])) for e in events})
    return result


def paired_difference(left, right, k):
    """Compare methods over shared weeks with the same sampled week multiplicities."""

    def blocks(rows):
        result = defaultdict(list)
        for row in unique_song_weeks(
            [r for r in rows if r["rank"] <= k and r["mature"]]
        ):
            result[week(row["day"])].append(float(row["hit"]))
        return result

    a, b = blocks(left), blocks(right)
    keys = sorted(a.keys() & b.keys())
    n = len(
        {
            (row["song"], week(row["day"]))
            for row in [*left, *right]
            if row["rank"] <= k and row["mature"] and week(row["day"]) in keys
        }
    )
    if not keys:
        return {"value": None, "n": 0, "weeks": 0, "interval": None}

    def delta(sample):
        return mean([v for key in sample for v in a[key]]) - mean(
            [v for key in sample for v in b[key]]
        )

    rng = random.Random(SEED)
    samples = (
        [delta(rng.choices(keys, k=len(keys))) for _ in range(1000)]
        if len(keys) >= 2
        else []
    )
    return {
        "value": delta(keys),
        "n": n,
        "weeks": len(keys),
        "interval": [quantile(samples, 0.025), quantile(samples, 0.975)]
        if samples
        else None,
    }

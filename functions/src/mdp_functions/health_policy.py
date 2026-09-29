"""Health defaults shared by runtime receipts, alerts and the console."""

CADENCE_OVERDUE_INTERVALS = {"hourly": "1 hour", "daily": "1 day", "weekly": "7 days"}
# Probe execution has one budget; HTTP allows time to return its outcome.
CANARY_TIMEOUT_S = 25
CANARY_HTTP_GRACE_S = 5
MIN_ROW_COVERAGE = 0.5
# Legacy canary prefixes remain excluded; current dry-run probes create no cycles.
AD_HOC_CYCLE_PREFIXES = ("manual:", "backfill:", "canary:")
COVERAGE_DEFAULT = {"large_set_size": 10, "large_set_floor": 0.9, "small_set_floor": 1.0}


def default_coverage_floor(total: int) -> float:
    return (
        COVERAGE_DEFAULT["large_set_floor"]
        if total >= COVERAGE_DEFAULT["large_set_size"]
        else COVERAGE_DEFAULT["small_set_floor"]
    )


def cadence_overdue_sql(column: str) -> str:
    """Render a SQL expression for a trusted cadence column, never user input."""
    cases = " ".join(
        f"WHEN '{cadence}' THEN interval '{interval}'"
        for cadence, interval in CADENCE_OVERDUE_INTERVALS.items()
    )
    return f"CASE {column} {cases} ELSE interval '{CADENCE_OVERDUE_INTERVALS['weekly']}' END"


def scheduled_cycle_sql(column: str = "opened_by_dbt_run_id") -> str:
    """Cadence health counts scheduled cycles and their retries, never ad hoc work.

    The column is trusted SQL, never user input. Prefix comparisons avoid driver-specific
    percent escaping, so Python and the generated TypeScript use the same predicate.
    """
    return " AND ".join(
        f"left({column}, {len(prefix)}) <> '{prefix}'"
        for prefix in AD_HOC_CYCLE_PREFIXES
    )

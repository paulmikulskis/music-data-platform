"""Target delivery and accepted rows are separate coverage measurements."""

from typing import Any

from mdp_functions.health_policy import MIN_ROW_COVERAGE, default_coverage_floor


def target_coverage(run: dict[str, Any], batches: list[dict[str, Any]]) -> dict[str, Any]:
    policy = (run.get("resolved_config") or {}).get("target_coverage")
    if policy is None:
        return {}
    targets, succeeded = set(), set()
    for batch in batches:
        members = {str(t) for t in batch["target_ids"]}
        if not members and policy.get("untargeted"):
            # Untargeted bronze executes once with the synthetic identity "".
            members = {""}
        completed = set((batch.get("cursor_checkpoint") or {}).get("completed_targets", []))
        failed = {t.split(":", 1)[1] for t in completed if t.startswith(("stale_target:", "rejected:"))}
        skipped = {t.removeprefix("skipped:") for t in completed if t.startswith("skipped:")}
        targets.update(members - skipped)
        succeeded.update((members & completed) - failed - skipped)
    total = len(targets)
    floor = policy.get("min_target_coverage")
    if floor is None:
        floor = default_coverage_floor(total)
    achieved = len(succeeded) / total if total else 1.0
    return {"min_target_coverage": floor, "target_coverage": achieved,
            "targets_succeeded": len(succeeded), "targets_total": total,
            "target_coverage_met": achieved >= floor}


def exclusion_counts(pages: list[dict[str, Any]]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for page in pages:
        for reason, count in page["attrs"].get("exclusions", {}).items():
            counts[reason] = counts.get(reason, 0) + int(count)
    return counts


def rejection_count(pages: list[dict[str, Any]]) -> int:
    """Read both current counters and retained pages that included exclusions."""
    return sum(
        int(page["attrs"]["rejected"])
        - (sum(int(n) for n in page["attrs"].get("exclusions", {}).values())
           if page["attrs"].get("accounting_version", 1) < 2 else 0)
        for page in pages
    )


def row_coverage(written: int, rejected: int, floor: float = MIN_ROW_COVERAGE,
                 exclusions: dict[str, int] | None = None) -> dict[str, Any]:
    excluded = sum((exclusions or {}).values())
    total = written + rejected
    accepted = written / total if total else None
    return {
        "rows_excluded": str(excluded),
        "row_exclusions": exclusions or {},
        "row_coverage": "partial" if rejected else ("full" if written else "empty"),
        "row_acceptance": accepted,
        "row_rejection_share": rejected / total if total else None,
        "min_row_coverage": floor,
        "row_coverage_met": accepted >= floor if accepted is not None else True,
    }


def zero_yield_targets(batches: list[dict[str, Any]]) -> list[str]:
    """Completed targets with filtered rows but no validated output across any page or retry.

    Checkpoints retain accepted, excluded and rejected markers after page buffers clear.
    Untargeted sources have no target id and do not enter this check.
    """
    completed, accepted, filtered, skipped = set(), set(), set(), set()
    for batch in batches:
        markers = set((batch.get("cursor_checkpoint") or {}).get("completed_targets", []))
        members = {str(t) for t in batch["target_ids"]}
        completed.update(members & markers)
        skipped.update(t.removeprefix("skipped:") for t in markers if t.startswith("skipped:"))
        accepted.update(t.removeprefix("accepted:") for t in markers if t.startswith("accepted:"))
        filtered.update(t.split(":", 1)[1] for t in markers if t.startswith(("excluded:", "row_rejected:")))
    return sorted((completed & filtered) - accepted - skipped)

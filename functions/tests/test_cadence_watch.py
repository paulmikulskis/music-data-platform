"""The external watch fails on missing, overdue and stale status."""

import importlib.util
from datetime import UTC, datetime
from pathlib import Path

import pytest

spec = importlib.util.spec_from_file_location("cadence_watch", Path(__file__).parents[2] / "ops/check-cadence.py")
watch = importlib.util.module_from_spec(spec)
spec.loader.exec_module(watch)


@pytest.mark.parametrize("payload", [
    {}, {"ok": False, "overdue": ["hourly"]}, {"ok": True, "overdue": ["daily"]},
    {"ok": True, "overdue": [], "checked_at": "2026-09-26T00:00:00Z"},
    {"ok": True, "overdue": [], "checked_at": "2026-09-26T12:00:00"},
])
def test_unhealthy_or_stale(payload):
    with pytest.raises(ValueError):
        watch.check(payload, datetime(2026, 9, 26, 12, tzinfo=UTC))


def test_fresh_status():
    watch.check({"ok": True, "overdue": [], "checked_at": "2026-09-26T12:00:00Z"},
                datetime(2026, 9, 26, 12, tzinfo=UTC))

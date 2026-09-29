"""Preserve read-only audits before integration fixtures drop their disposable DBs."""

import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
from audit import audit


@pytest.hookimpl(tryfirst=True)
def pytest_runtest_teardown(item, nextitem):
    rt = item.funcargs.get("rt")
    if rt is None or not os.environ.get("MDP_ADVERSARIAL_AUDIT"):
        return
    # Runtime fixtures intentionally left pending (permit tests) have no dumps.
    # Each replay's loaded outputs are audited before databases disappear.
    audit(rt.settings.control_url, rt.settings.warehouse_url)

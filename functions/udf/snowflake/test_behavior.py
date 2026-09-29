"""Execute the embedded handler without a Snowflake account."""
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest


def handler(monkeypatch, polls, timeouts=0):
    state = {"polls": 0, "time": 0}
    class Session:
        def __init__(self):
            self.headers = {}
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def request(self, method, path, **kwargs):
            if method == "POST": result = {"run_id": "fixture"}
            else:
                state["polls"] += 1
                if state["polls"] <= timeouts:
                    raise RequestException("read timed out")
                result = polls[min(state["polls"] - timeouts - 1, len(polls) - 1)]
            return SimpleNamespace(status_code=200, json=lambda: result)
    class RequestException(Exception): pass
    monkeypatch.setitem(sys.modules, "requests", SimpleNamespace(Session=Session, RequestException=RequestException, ConnectionError=RequestException))
    monkeypatch.setitem(sys.modules, "_snowflake", SimpleNamespace(get_generic_secret_string=lambda key: "https://fixture.invalid" if key == "service_url" else "fixture"))
    namespace = {}
    exec(Path(__file__).with_name("mdp_invoke.sql").read_text().split("AS $$", 1)[1].split("$$;", 1)[0], namespace)  # noqa: S102 - execute checked-in handler in a mocked module environment
    namespace["time"] = SimpleNamespace(monotonic=lambda: state["time"], sleep=lambda seconds: state.update(time=state["time"] + seconds))
    return namespace["Invoke"](), state


def poll(pending=0, load="loaded"):
    return {"run": {"status": "succeeded"}, "repairs_pending": pending, "receipts": [{"run_id": "fixture", "status": "succeeded", "coverage": "full", "rows_written": "1", "rows_rejected": "0", "loads": [{"status": load}]}]}


def test_waits_for_repairs(monkeypatch):
    invoke, state = handler(monkeypatch, [poll(1), poll(0)])
    assert len(list(invoke.process("fixture", {}))) == 1
    assert state["polls"] == 2


def test_repairs_obey_deadline(monkeypatch):
    invoke, _ = handler(monkeypatch, [poll(1)])
    with pytest.raises(RuntimeError, match="invoke_timeout"):
        list(invoke.process("fixture", {"deadline_s": 3}))


@pytest.mark.parametrize("status", ["pending", "claimed", "rejected"])
def test_invalid_final_load_fails(monkeypatch, status):
    invoke, _ = handler(monkeypatch, [poll(load=status)])
    with pytest.raises(RuntimeError, match="load_incomplete"):
        list(invoke.process("fixture", {}))


def test_two_poll_timeouts_then_answer(monkeypatch):
    invoke, state = handler(monkeypatch, [poll()], timeouts=2)
    assert len(list(invoke.process("fixture", {}))) == 1
    assert state["polls"] == 3 and state["time"] == 1.5


def test_three_consecutive_poll_timeouts_fail(monkeypatch):
    invoke, state = handler(monkeypatch, [poll()], timeouts=3)
    with pytest.raises(RuntimeError, match="run polling failed 3 times in a row"):
        list(invoke.process("fixture", {}))
    assert state["polls"] == 3


def failed(blocks_cycle=True):
    receipt = {"run_id": "fixture", "status": "failed", "coverage": "partial", "rows_written": "38",
               "rows_rejected": "0", "loads": [], "error_class": "invoke_timeout", "target_coverage_met": False,
               "blocks_cycle": blocks_cycle, "message": "Attempt deadline expired; Target coverage 38/48 is below the floor 90%"}
    return {"run": {"status": "failed"}, "repairs_pending": 0, "receipts": [receipt]}


def test_failed_run_reports_its_receipt(monkeypatch):
    invoke, _ = handler(monkeypatch, [failed()])
    with pytest.raises(RuntimeError, match=r"^invoke_timeout: Attempt deadline expired; .*38/48.*\. Open /runs/fixture$"):
        list(invoke.process("fixture", {}))


def test_non_blocking_source_returns_its_failed_receipt(monkeypatch):
    invoke, _ = handler(monkeypatch, [failed(blocks_cycle=False)])
    rows = list(invoke.process("fixture", {}))
    assert [(r[1], r[2], r[3]) for r in rows] == [("failed", "partial", 38)]


def test_deadline_names_the_running_run(monkeypatch):
    running = {"run": {"status": "running"}, "receipts": [{"targets_succeeded": 27, "targets_total": 48}]}
    invoke, _ = handler(monkeypatch, [running])
    with pytest.raises(RuntimeError, match=r"^invoke_timeout: run fixture still running at the 5s invocation "
                       r"deadline; last receipt 27/48 targets\. Open /runs/fixture$"):
        list(invoke.process("fixture", {"deadline_s": 5}))

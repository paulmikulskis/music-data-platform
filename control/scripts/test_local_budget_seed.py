"""Local bootstrap budget policy, without running Compose or changing the stack."""
from contextlib import closing
import os
import pytest
import psycopg
from mdp_functions.cli import seed_local_budgets

pytestmark = pytest.mark.skipif(os.environ.get("MDP_CONTROL_INTEGRATION") != "1", reason="isolated database required")

def test_seed_is_idempotent_and_preserves_operator_caps(monkeypatch):
    monkeypatch.setenv("MDP_BUDGET_GLOBAL_CENTS", "10")
    monkeypatch.setenv("MDP_BUDGET_STREAMLINE_CENTS", "10")
    monkeypatch.setenv("MDP_BUDGET_LLM_STEP_CENTS", "10")
    monkeypatch.setenv("MDP_BUDGET_CEILING_CENTS", "20")
    # Roll back only this test's configuration, leaving the shared stack intact.
    with closing(psycopg.connect(os.environ["MDP_CONTROL_RT_URL"])) as conn:
        seed_local_budgets(conn)
        first = conn.execute("SELECT id,cap_cents,ceiling_cents FROM control.budget WHERE period='monthly' ORDER BY id").fetchall()
        assert first
        changed = first[0][0]
        conn.execute("UPDATE control.budget SET cap_cents=11 WHERE id=%s", (changed,))
        seed_local_budgets(conn)
        second = conn.execute("SELECT id,cap_cents,ceiling_cents FROM control.budget WHERE period='monthly' ORDER BY id").fetchall()
        assert len(second) == len(first)
        assert conn.execute("SELECT cap_cents FROM control.budget WHERE id=%s", (changed,)).fetchone()[0] == 11
        assert conn.execute("SELECT count(*) FROM control.budget WHERE scope='streamline' AND period='monthly'").fetchone()[0] == conn.execute("SELECT count(*) FROM control.streamline").fetchone()[0]
        conn.rollback()


def test_seed_refuses_cap_above_ceiling(monkeypatch):
    monkeypatch.setenv("MDP_BUDGET_GLOBAL_CENTS", "21")
    monkeypatch.setenv("MDP_BUDGET_CEILING_CENTS", "20")
    with closing(psycopg.connect(os.environ["MDP_CONTROL_RT_URL"])) as conn:
        with pytest.raises(ValueError, match="within ceiling"):
            seed_local_budgets(conn)
        conn.rollback()

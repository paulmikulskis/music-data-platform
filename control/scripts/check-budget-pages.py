"""Exercise the operator budget flow through real PRG forms."""
import json
import os
from pathlib import Path
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from uuid import uuid4

base = os.environ.get("MDP_CONTROL_API_URL", "http://127.0.0.1:8090")
period = "control-persona-" + str(uuid4())

def submit(action, fields):
    with urlopen(Request(base + "/actions/" + action, data=urlencode(fields).encode())) as response:
        body = response.read().decode()
        assert "/ops?result=" in response.url, response.url
        assert "Raw result" in body
        assert 'class="card error"' not in body
        return body

submit("create-budget", {"scope":"global","scope_id":"","period":period,"cap_cents":"100","soft_pct":"80","hard_action":"warn","ceiling_cents":"200"})
with urlopen(base + "/api/budgets") as response:
    budget = next(b for b in json.load(response) if b["period"] == period)
body = submit("budget", {"id":budget["id"],"cap_cents":"150"})
assert "Raise within ceiling" in body and "New cap (cents)" in body
with urlopen(base + "/api/budgets") as response:
    raised = next(b for b in json.load(response) if b["id"] == budget["id"])
assert raised["cap_cents"] == "150" and raised["ceiling_cents"] == "200"
Path("ops/evidence/control/fixes-budget-flow.txt").write_text("POST /actions/create-budget -> 303 -> /ops?result=<audit-id>\nPOST /actions/budget -> 303 -> /ops?result=<audit-id>\nVerified created budget, unchanged ceiling, successful raise, New cap (cents) input and Raise within ceiling button. Synthetic amounts omitted.\nBUDGET_FLOW PASS\n")
print("BUDGET_FLOW PASS evidence=ops/evidence/control/fixes-budget-flow.txt")

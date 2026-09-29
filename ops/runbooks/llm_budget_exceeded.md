# llm_budget_exceeded

Symptom: enrichment stops with partial coverage; committed output and retained inputs remain available.

First query:

```sql
select id,status,error_class,cost_cents,input_dump_id
from control.run where error_class='llm_budget_exceeded' order by created_at desc limit 20;
```

The button: Review the step budget and its reservation; raise within the approved ceiling or publish a lower-cost configuration. Use LLM rerun for a reviewed configuration and Sync proxy costs to reconcile estimates.

Escalate when the approved ceiling is insufficient, proxy authentication cannot be restored,
or repeated calls disagree with the authoritative ledger. Never discard committed dumps.

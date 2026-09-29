# cost_cap_hit

Symptom: enrichment stops with partial coverage; committed output and retained inputs remain available.

First query:

```sql
select id,status,error_class,cost_cents,input_dump_id
from control.run where error_class='cost_cap_hit' order by created_at desc limit 20;
```

The button: Inspect consumed and reserved amounts, then choose the approved warn, pause or degrade policy. Use LLM rerun for a reviewed configuration and Sync proxy costs to reconcile estimates.

Escalate when the approved ceiling is insufficient, proxy authentication cannot be restored,
or repeated calls disagree with the authoritative ledger. Never discard committed dumps.

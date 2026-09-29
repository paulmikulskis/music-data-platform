# control_api_unavailable


## Symptom

A promoter cannot reach control-api or receives a refused/error response. Its run fails
instead of leaving target changes untracked.


## First query

```sql
SELECT a.id, a.run_id, a.subject_type, a.subject_id, a.opened_at,
       r.status, r.error_message, s.source_key
FROM control.alert a
LEFT JOIN control.run r ON r.id = a.run_id
LEFT JOIN control.streamline s ON s.id = r.streamline_id
WHERE a.class = 'control_api_unavailable' AND a.resolved_at IS NULL
ORDER BY a.opened_at DESC LIMIT 20;
```


## The button

Follow the promoter run trace from `/ops`. Check mdp-control-api health and private reachability
from mdp-functions. The configuration owner verifies `MDP_CONTROL_API_URL` and the configured
`MDP_CONTROL_API_KEY` without printing its value; the key needs the promoter role and permitted
target commands. Restore the matching API/configuration, then request a fresh attempt.
Promoters use the control API for writes; do not bypass it with SQL.


## When to escalate

The matching healthy API still refuses the promoter, or audit outcomes do not explain a target
change. Give the control maintainer the run id, response status and audit correlation; omit tokens
and provider payloads.

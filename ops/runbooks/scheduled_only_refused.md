# scheduled_only_refused


## Symptom

A `scheduled_only` function receives a manual admission, a `manual:` work key, or a cycle opened
by `manual:` or `backfill:`. Admission returns 409 before reading inputs, so an alert/run row
need not exist. Retries and Replays attached to a scheduled cycle pass this service gate;
the separate tenant Replay gate still refuses non-global scope.


## First query

```sql
SELECT id, cadence, scope, status, opened_by_dbt_run_id, opened_at, closed_at
FROM control.cycle
ORDER BY opened_at DESC LIMIT 20;
```


## The button

Use the request's cycle id to check its opener and the source declaration. Wait for the
scheduled cycle for a new digest or call freeze. Use Retry of that scheduled cycle for
incomplete work; do not replace it with Run Now or backfill and do not remove `scheduled_only`.
Completed per-input work stays complete, including an empty weekly call.


## When to escalate

A genuine scheduled cycle is refused, or Retry changes an already frozen call/send. Give the
runtime maintainer the source key, cycle id, dbt run id and request error, without credentials.

# scope_mismatch


## Symptom

The service returns HTTP 409 with `error_class=scope_mismatch`, or a built-in
run records that class. A close request can target a superseded cycle; admission
can also disagree with the immutable binding's scope, cadence, runner or job.
The service maps this class to the `scope-mismatch` runbook slug.

`Export target membership before invoking this source` means the cycle holds no
frozen membership the source may collect: the run that opened the cycle has not
exported the source's kind, or an export that listed the kind while its target
set existed froze nothing for it. A restore, Retry or Replay of a cycle whose
export froze before the source or its target set existed does not raise this
class: the invoke records `not_in_cycle`, succeeds empty, and the close proceeds.


## First query

Use the configured control connection and substitute the bound dbt run identifier:

```sql
SELECT a.dbt_run_id, a.cycle_id, a.runner, a.job_id,
       c.status, c.cadence, c.scope,
       j.runner AS job_runner, j.cadence AS job_cadence, j.scope AS job_scope
FROM control.cycle_attempt a
JOIN control.cycle c ON c.id = a.cycle_id
JOIN control.dbt_job j ON j.job_id = a.job_id
WHERE a.dbt_run_id = '<dbt-run-id>';
```

Compare the error message and request with these values. For a direct close
without a dbt run identifier, look up the requested UUID in `control.cycle`.


## The button

Correct the request to match its existing binding, then retry the invocation or
`POST /v1/cycles/{cycle_id}/close`. There is no separate operator UI button yet.
For a superseded cycle, use the authorized current dbt attempt and its own
binding; never reopen the superseded cycle or change an immutable binding.


## When to escalate

Escalate if a request matches its registered job and immutable binding but is
still refused, or supersession occurred without the expected scheduled run.
Include the error class, cycle identifier, and compared scope/cadence values.

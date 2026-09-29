# Source canary failed

After a deploy, `ops/fly/resilience-checks.py` asks the control API to probe each enabled source once.
A probe is a dry run: it fetches a small sample of live inputs with the new code, checks the records
against the source's declared schema, and keeps only an audit row. It opens no run or cycle and writes
no dump, raw row or cursor, so it never changes scheduled work.

Only sources that declare `canary=True` are probed; that flag means their probe requests are free
public requests. Every other source reports `skipped` with its reason. `not_due` means the source had
no due target.

1. Read the failed line in the report, or the newest `source.canary` row for the source in the audit
   log. It names the source, the error class and the function page.
2. Open the function page and fix the reported cause. For `unknown_source`, the streamline is enabled
   but no function by that key is deployed: deploy the function, or disable the streamline.
3. Rerun the probes: `secret_store run -- uv run --project functions python ops/fly/resilience-checks.py`.
   A passing probe resolves nothing by itself; acknowledge the warning once the cause is fixed.

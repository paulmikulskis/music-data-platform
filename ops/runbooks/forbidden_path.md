# forbidden_path


## Symptom

A run records `error_class=forbidden_path`, with a dead letter naming the refused host and path. A function,
an auth flow, an event hook, or a redirect asked for a path on the runtime forbidden-path list
(`functions/src/mdp_functions/fetch/forbidden.py`: DSP web-player token endpoints, the internal GraphQL
API, spclient hosts, the Apple web app catalog API), or sent a Host header naming another authority. The
refusal happens in the client transport, before any byte leaves the service.

Cookie headers and jar entries are also refused: fetch the public page and observe only Set-Cookie
names. Model calls outside gold are refused: land the original text in bronze, then use a budgeted
gold `llm_step`. See [the source guide](../../docs/operating.md#what-the-platform-refuses).


## First query

```sql
SELECT d.run_id, d.target_id, d.reason, d.created_at, s.source_key
FROM control.dead_letter d
JOIN control.streamline s ON s.id = d.streamline_id
WHERE d.reason LIKE 'forbidden_path:%'
ORDER BY d.created_at DESC LIMIT 20;
```


## The button

None: a forbidden path is never retried or allowed through a knob. Change the collector so it reads the
public, server-rendered surface its manifest declares, and check that no redirect it follows leads to a
listed path. Pause the streamline on `/functions/:source_key` until the fix deploys.


## When to escalate

A public surface itself now redirects to a listed path, or recon records a new token endpoint on another
DSP. Add the endpoint to the list in a PR with its example URL in `functions/tests/test_forbidden_paths.py`.

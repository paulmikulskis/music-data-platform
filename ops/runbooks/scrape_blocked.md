# scrape_blocked


## Symptom

A response matches a block signature or an authentication refusal. The host pauses, and later
targets on that host fail fast without another request.


## First query

```sql
SELECT host, blocked_until, last_signature, updated_at
FROM control.host_health
WHERE blocked_until > now()
ORDER BY updated_at DESC LIMIT 20;
```


## The button

Open the run trace from `/ops` and inspect the host and recorded signature. Stop collection on
that host while the source owner checks the permitted public surface. Keep a collector disabled
when that surface refuses the honest user agent. Never change exits, proxy tier, user agent or
credentials to retry a block; the expiry of a host pause is not permission to bypass it.


## When to escalate

The host remains blocked or needs login, lifted tokens or a forbidden endpoint. Give the source
owner the host, signature and run id for a collection decision; keep the source off.

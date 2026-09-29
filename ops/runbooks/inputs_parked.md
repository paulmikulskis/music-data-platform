# inputs_parked


## Symptom

A gold read leaves out inputs that fail individually in its declared `park_after` count of runs
within 28 days. One alert stays open per streamline; ten or more parked inputs make it a warning.
Vendor outages, an opened circuit and a drift streak do not park inputs.


## First query

```sql
SELECT e.run_id, e.at, e.attrs
FROM control.run_event e
JOIN control.run r ON r.id = e.run_id
WHERE e.event_type = 'inputs_parked'
  AND r.streamline_id IN (
    SELECT ar.streamline_id FROM control.alert a
    JOIN control.run ar ON ar.id = a.run_id
    WHERE a.class = 'inputs_parked' AND a.resolved_at IS NULL
  )
ORDER BY e.at DESC LIMIT 20;
```


## The button

Inspect the parked input references and their earlier `inputs_rejected` events on the function
trace. Correct the target or parser, then use the function page's release button or
`pnpm --dir control mdp unpark <key>`. This resolves the alert; the next read counts only
failures after that release and tries the inputs again. Acknowledging the alert alone does not
release inputs. Failures also age out after 28 days.


## When to escalate

Inputs park again after the cause is corrected, or unrelated valid inputs disappear from reads.
Give the function maintainer the source key, input_ref/input_version and run ids.

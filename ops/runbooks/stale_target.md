# Stale target

A target returns HTTP 404 or 410. Its first stale hit opens one warning.
The run continues with the other frozen targets. The receipt shows the coverage floor.

Two consecutive attempted scheduled cycles can park the target. Healthy peers from
the same source and host must succeed in both cycles. Non-due weekly targets do not
break the streak. Each cycle can park one target or ten percent of its eligible set, whichever is larger. An outage or a
reached limit opens a warning and leaves remaining targets active. A source can declare
`stale_target_cycles` to choose another count. Retries count as the same cycle.
Parking deactivates the target and writes `targets.parkStale` with the reason and count
in the audit log. Existing cycle membership stays frozen. New cycles leave it out.

Check the target's public URL. Correct its spec if the URL moved. Then run:

```sh
pnpm --dir control mdp targets reactivate <target-id>
```

Import and activation record “not probed” and proceed without a network request.
The control worker checks pending targets each minute within a 15-second request budget.
Unavailable checks stay pending. `mdp targets probe <set>` checks a whole
set explicitly through the usual host cap. A failed check never fails a promoter.
Weekly targets can be probed on any day.

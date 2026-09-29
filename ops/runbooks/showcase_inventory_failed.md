# Warehouse inventory

Open `/ops` and follow the `showcase_inventory_failed` alert to its warehouse ID.
The snapshot job cannot save the warehouse inventory. Missing days stay unavailable.
A snapshot never substitutes rows collected for rows currently held.

1. Inspect the showcase snapshot job logs for that warehouse and UTC day.
2. Check its warehouse connection and the `showcase_wh` role. It reads catalog metadata only.
3. Check the control connection. `control_rt` writes `control.showcase_inventory`.
   It reports failures through the authenticated functions endpoint
   `POST /v1/alerts/showcase_inventory_failed` with `{"warehouse":"<warehouse UUID>"}`.
   `functions_rt` opens one unresolved alert per warehouse.
4. Retry the snapshot capture. Keep its capture time and mark unknown row estimates incomplete.
   Do not create snapshots for missed days.
5. Verify the new snapshot with `pnpm --dir control mdp platform holdings --since <UTC timestamp>`.
   Resolve the alert in `/ops` after a successful capture.

# Console access

Ask an operator for a staff key to use the console's read and analysis pages.
The operator runs `pnpm --dir control mdp keys create --role staff --label <text>`.
Send the returned key privately. Pass it in the `x-api-key` header on console requests.
For browser setup, follow [Reach control-api](../../docs/operating.md#reach-control-api).

Staff can run queries, preview models, backtest and save a reviewed draft as a PR.
Warehouse reads use the global analyst boundary. Raw and tenant previews need admin.
Use global staging, intermediate or marts in Workbench, or ask an operator to inspect restricted data.

Tenant, key, target, budget, knob, recovery, runner and alert changes need admin.
Sandbox operator controls and archives also need admin. Ask an operator to perform the action.
Operators revoke a key with `pnpm --dir control mdp keys revoke <id>`.

To link Sandbox status, add `--warehouse-role analyst_<handle>` when issuing the key.
For Clerk, set `mdp_staff: true` and `mdp_warehouse_role: "analyst_<handle>"` in public metadata.
The mapping shows only that analyst's sandbox; it grants no SQL login. Ask the operator to verify the owner.

# Invocation models

This directory contains generated, editable export/invoke/close tables for each cadence and
scope, plus silver/gold/universal invoke models assigned to their transform selectors.
Export freezes target membership; each bronze invoke depends on export; close depends on
export and all bronze invokes in the same cadence/scope. Data staging depends on close.
Only function invokes carry the invoke tag, and each has a same-cadence dependent.
Postgres uses the transactional mdp_statement_timeout pre-hook. Local/empty/dry-run invokes
read fixture receipts without HTTP. See [dbt conventions](../../CLAUDE.md).

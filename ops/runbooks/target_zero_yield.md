# Target without output

A target completed but every observed row was excluded or rejected. This warning does not fail
its run or park the target. Open the target from the alert, then open its producing run.

1. Inspect the run receipts and rejected records. Compare the exclusion reasons with
   `exclusion_reasons` in the source declaration.
2. Check the provider page. If real rows were classified as non-track items or another expected
   class, fix the collector and its fixture test, then retry the cycle.
3. If the target has no supported content, resolve the warning in `/ops#alerts`.
   If it no longer belongs in collection, deactivate it from the target page.

`ctx.exclude()` is for known non-error classes, such as a playlist episode.
Use `ctx.reject()` for missing fields or unknown item kinds.
See [page accounting](../../functions/CLAUDE.md#page-accounting) before editing a collector.

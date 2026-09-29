# Undeclared exclusion

The collector calls `ctx.exclude()` with a reason absent from its declaration.
Use `ctx.reject()` for missing fields or unknown item kinds. For a stable non-error class,
add the exact reason to `exclusion_reasons` on the source decorator and test a fixture.
Then retry the cycle. See [page accounting](../../functions/CLAUDE.md#page-accounting).

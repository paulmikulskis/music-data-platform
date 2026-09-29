# chart-momentum

Question: How often do songs in `mart_chart_history` hold a top-ten place from one week to the next?
Owner handle: `example`. Inputs: `marts.mart_chart_history`.
Status: example.

The fit is illustrative. Use this folder to compare your own scaffold.

Run `source("setup.R")` once after clone or pull. Set `MDP_DB` to a libpq service
(`mdp` or `mdp_local`) or a `.duckdb` snapshot path. Run
`testthat::test_dir("tests/testthat")`, then render `explore.qmd` with parameter `db`.
The optional pipeline runs with `targets::tar_make()`; delete `_targets.R` if unused.
Commit source, SQL, tests, `renv.lock` and `models.yml`. Keep data and rendered reports out of git.

Continue with [Save work](../../../docs/r.md#save-work).

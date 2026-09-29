# R conventions

`mdpr` connects R to the warehouse and helps analysts propose reusable work.
Start with the [R guide](../docs/r.md).


## Concepts, in order

1. **Service name** selects a libpq connection without putting credentials in code.
2. **Lazy query** keeps filtering in SQL until `collect()` brings rows into R.
3. **Rights columns** carry learning eligibility, resale permission and source keys.
4. **Analysis folder** keeps source, tests and model provenance under `analyses/<handle>/<topic>/`.
5. **Pin** stores a fitted model locally with its input and environment metadata.


## Ownership

- `mdpr/R/` owns connection helpers, rights guards and proposal helpers.
- `mdpr/inst/templates/` owns analysis scaffolds; `mdpr/tests/` checks package behavior.
- Analysts own their [analysis projects](../analyses/README.md). Data, credentials and fitted models stay outside git.
- dbt owns production joins, aggregates and served marts. Python owns production record shaping.
  R has no production runtime. Follow [Propose a change](../docs/r.md#propose-a-change) to hand off SQL.
- Generated files keep their generator headers. Regenerate them from their declared inputs.


## A small real query

After [setup](../docs/r.md#set-up), run this from R:

```r
con <- mdpr::mdp_connect()
q <- mdpr::mdp_tbl(con, "mart_chart_history") |>
  dplyr::filter(chart_position <= 10)
mdpr::mdp_check_rights(q)
rows <- dplyr::collect(q)
DBI::dbDisconnect(con)
```

The query keeps the mart's grain and rights columns. The rights check verifies those columns
are present; filter training rows with `mdp_learnable()` as shown in [Models](../docs/r.md#models).
Run package checks from the repository root with `bash ops/r/check.sh --quick`.

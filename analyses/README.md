# Analyses

Each project lives at `analyses/<handle>/<topic>/`. Create it with
`mdpr::mdp_new_analysis("topic")`; follow the [R guide](../docs/r.md).
Commit R code, SQL, report source, tests, `renv.lock` and `models.yml`.
Data files, pins, snapshots, rendered reports, credentials and notebook output stay out of git.
The analysis guard refuses data formats, credentials, executed notebooks, CSV files over
1 MB and any file over 5 MB, including tracked files that later become ignored.

[chart-momentum](example/chart-momentum/) passes the guard and the R checks; scaffold your own and compare.

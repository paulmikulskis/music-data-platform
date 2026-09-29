test_that("known and future platform errors have recovery guidance", {
  catalog <- jsonlite::fromJSON(system.file("extdata", "error_catalog.json", package = "mdpr"))
  for (code in names(catalog)) expect_identical(mdp_error_hint(code), catalog[[code]]$next_step)
  expect_identical(mdp_error_hint("future_code"), catalog$unmapped$next_step)
  expect_identical(mdp_error_hint(NA_character_), catalog$unmapped$next_step)
})

test_that("a missing local table keeps its database message and recovery", {
  con <- DBI::dbConnect(duckdb::duckdb())
  on.exit(DBI::dbDisconnect(con, shutdown = TRUE))
  expect_error(mdp_tbl(con, "absent", schema = "main"), "absent", class = "model_not_built")
  expect_error(mdp_tbl(con, "absent", schema = "main"), "ops/local/up.sh")
  expect_error(mdp_tbl(con, "absent", schema = "tenant_demo_marts"), "demo-tenant", class = "tenant_read_denied")
  expect_error(mdp_tbl(con, "absent", schema = "tenant_demo_marts"), "Tenant data is not available to this role")
})

test_that("tenant refusals do not expose whether the relation exists", {
  detail <- "permission denied for schema tenant_private_marts"
  local_mocked_bindings(tbl = function(...) stop(detail), .package = "dplyr")
  denied <- tryCatch(mdp_tbl(NULL, "probe", schema = "tenant_private_marts"), error = identity)
  detail <- 'relation "tenant_private_marts.probe" does not exist'
  missing <- tryCatch(mdp_tbl(NULL, "probe", schema = "tenant_private_marts"), error = identity)
  expect_identical(class(denied), class(missing))
  expect_identical(conditionMessage(denied), conditionMessage(missing))
  expect_null(denied$parent)
  expect_null(missing$parent)
})

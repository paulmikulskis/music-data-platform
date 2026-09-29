library(mdpr)
dir.create(Sys.getenv("HOME"), showWarnings = FALSE)
Sys.setenv(PGSERVICEFILE = file.path(Sys.getenv("HOME"), "service"), PGPASSFILE = file.path(Sys.getenv("HOME"), "password"))
mdp_setup(target = "local", port = 5432)
pane <- NULL
options(connectionObserver = list(connectionOpened = function(...) pane <<- list(...), connectionClosed = function(...) NULL))
con <- mdp_connect("mdp_local")
assert <- function(ok, label) { stopifnot(isTRUE(ok)); cat("PASS", label, "\n") }
if (Sys.getenv("MDP_R_CLEANUP") == "1") {
  if (DBI::dbGetQuery(con, "SELECT to_regnamespace('sandbox_local') IS NOT NULL AS present")$present) {
    DBI::dbExecute(con, "DROP VIEW IF EXISTS sandbox_local.r_smoke_view")
    DBI::dbExecute(con, "DROP TABLE IF EXISTS sandbox_local.r_smoke_table")
  }
  DBI::dbDisconnect(con)
  quit(status = 0)
}
assert(DBI::dbGetQuery(con, "SELECT current_user")[[1]] == "analyst_local", "service-file login")
assert(typeof(DBI::dbGetQuery(con, "SELECT 9007199254740991::bigint AS n")$n) == "double", "bigint numeric")
assert(DBI::dbGetQuery(con, "SHOW timezone")[[1]] == "UTC", "UTC timezone")
assert("marts" %in% pane$listObjects()$name, "pane schemas")
assert(nrow(pane$previewObject(schema = "marts", table = "mart_chart_history", limit = 2)) <= 2L, "pane preview limit")
# Execute the exact documented starter SQL, including its rights annotations.
text <- paste(readLines("docs/analyst-access.md"), collapse = "\n")
text <- strsplit(text, "## Ten starter queries", fixed = TRUE)[[1]][2]
blocks <- regmatches(text, gregexpr("```sql\\n[\\s\\S]*?```", text, perl = TRUE))[[1]]
for (index in c(1L, 10L)) {
  sql <- sub("```$", "", sub("^```sql\\n", "", blocks[[index]]))
  assert(nrow(DBI::dbGetQuery(con, sql)) > 0L, paste("starter", index))
}
q <- mdp_tbl(con, "mart_chart_history") |> dplyr::filter(.data$chart_position <= 10L)
capture.output(sql <- mdp_sql(q))
assert(isTRUE(all.equal(as.data.frame(dplyr::collect(q)), DBI::dbGetQuery(con, sql), check.attributes = FALSE)), "SQL round trip")
# RPostgres exposes SQLSTATE in the server error text when verbose errors are enabled.
denial <- tryCatch({dplyr::copy_to(con, data.frame(x = 1), "r_smoke_temp"); NULL}, error = identity)
assert(inherits(denial, "error") && grepl("permission denied", conditionMessage(denial)), "copy_to TEMP denied")
DBI::dbExecute(con, "DO $$ BEGIN CREATE TEMP TABLE r_smoke_denial (x integer); RAISE EXCEPTION 'TEMP unexpectedly allowed'; EXCEPTION WHEN OTHERS THEN IF SQLSTATE <> '42501' THEN RAISE; END IF; END $$")
assert(TRUE, "TEMP denial SQLSTATE 42501")
rows <- dplyr::collect(q)
train <- mdp_learnable(rows)
assert(all(!is.na(train$learning_eligible) & train$learning_eligible), "learnable guard")
bad <- rows[1, ]; bad$learning_eligible <- FALSE
assert(inherits(tryCatch(mdp_pin_model(list(), "refused", bad), error = identity), "error"), "pin refusal")
local <- tempfile(); dir.create(local)
old <- setwd(local)
assert(!DBI::dbGetQuery(con, "SELECT has_schema_privilege(current_user, 'reference', 'USAGE') AS allowed")$allowed, "registry schema stays private")
assert(!DBI::dbGetQuery(con, "SELECT has_table_privilege(current_user, 'catalog.learning_rights', 'UPDATE') AS allowed")$allowed, "registry view stays read only")
# Nonempty synthetic training inputs carry a real eligible registry key.
eligible <- DBI::dbGetQuery(con, "SELECT source_key FROM catalog.learning_rights WHERE learning_eligible IS TRUE ORDER BY source_key LIMIT 1")$source_key
ineligible <- DBI::dbGetQuery(con, "SELECT source_key FROM catalog.learning_rights WHERE learning_eligible IS FALSE ORDER BY source_key LIMIT 1")$source_key
assert(length(eligible) == 1L && length(ineligible) == 1L, "registry fixtures")
train <- data.frame(value = 1, learning_eligible = TRUE, resale_permitted = FALSE,
                    source_keys = as.character(jsonlite::toJSON(eligible)))
tampered <- train[rep(1, 2), ]
tampered$source_keys <- as.character(jsonlite::toJSON(c(eligible, ineligible, "r_smoke_unknown")))
refused <- tryCatch(mdp_pin_model(list(), "tampered", tampered, con = con), error = identity)
assert(inherits(refused, "r_pin_rights_refused") && grepl("2 source keys", conditionMessage(refused)), "tampered and unknown keys refused once each")
assert(!grepl(ineligible, conditionMessage(refused), fixed = TRUE) && !grepl("r_smoke_unknown", conditionMessage(refused)), "refusal hides source values")
assert(!dir.exists("_pins") && !file.exists("models.yml"), "refusal writes no artifacts")
assert(inherits(tryCatch(mdp_pin_model(list(), "offline", train), error = identity), "r_pin_connection_required"), "offline pin refusal")
version <- mdp_pin_model(list(coefficient = 1), "smoke", train, con = con, inputs = "synthetic registry fixture")
assert(nzchar(version) && file.exists("models.yml"), "eligible model pin")
setwd(old)
sandbox <- DBI::dbGetQuery(con, "SELECT to_regnamespace('sandbox_local') IS NOT NULL AS present")$present
if (sandbox) {
  mdp_save_view(q, "r_smoke_view", con)
  mdp_write(rows, "r_smoke_table", con, overwrite = TRUE)
  assert(inherits(tryCatch(mdp_write(data.frame(x = 1), "r_smoke_bad", con), error = identity), "error"), "sandbox missing-rights refusal")
  assert(DBI::dbGetQuery(con, "SELECT count(*) FROM sandbox_local.r_smoke_table")[[1]] == nrow(rows), "sandbox write")
  file.create("/tmp/mdp-w-r/sandbox.ready")
}
if (nzchar(Sys.getenv("MDP_R_SNAPSHOT"))) {
  snapshot <- mdp_connect(Sys.getenv("MDP_R_SNAPSHOT"))
  assert(DBI::dbGetQuery(snapshot, "SELECT count(*) FROM marts.mart_shazam_chart_daily")[[1]] == DBI::dbGetQuery(con, "SELECT count(*) FROM marts.mart_shazam_chart_daily")[[1]], "snapshot count parity")
  assert(nrow(DBI::dbGetQuery(snapshot, "SELECT * FROM _mdp_snapshot")) > 0L, "snapshot manifest")
  assert(inherits(tryCatch(mdp_pin_model(list(), "snapshot", train, con = snapshot), error = identity), "r_pin_connection_required"), "snapshot pin requires warehouse connection")
  DBI::dbDisconnect(snapshot, shutdown = TRUE)
}
Sys.setenv(MDP_DB = "mdp_local")
testthat::test_dir("/tmp/mdp-w-r/template/analyses/local/template/tests/testthat", stop_on_failure = TRUE)
if (DBI::dbIsValid(con)) DBI::dbDisconnect(con)

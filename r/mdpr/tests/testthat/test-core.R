mock_warehouse_connection <- function() {
  loadNamespace("RPostgres")
  methods::new("PqConnection")
}

local_files <- function(env = parent.frame()) {
  home <- withr::local_tempdir(.local_envir = env)
  withr::local_envvar(c(PGSERVICEFILE = file.path(home, "service"), PGPASSFILE = file.path(home, "password")), .local_envir = env)
  home
}

rights_frame <- function() data.frame(id = 1:3, learning_eligible = c(TRUE, FALSE, NA), resale_permitted = TRUE, source_keys = '["eligible"]')

test_that("setup replaces blocks and entries privately without prompting locally", {
  local_files()
  local_mocked_bindings(prompt_password = function() stop("Unexpected prompt"))
  mdp_setup(target = "local")
  mdp_setup(target = "local")
  expect_equal(sum(readLines(libpq_path()) == "[mdp_local]"), 1)
  expect_length(readLines(libpq_path(TRUE)), 1)
  expect_true(has_password("mdp_local"))
  expect_equal(service_config("mdp_local")$port, "56432")
  if (.Platform$OS.type != "windows") expect_equal(as.integer(file.info(libpq_path(TRUE))$mode), 384L)
  mdp_setup(target = "local", port = 58532)
  expect_equal(service_config("mdp_local")$port, "58532")
})

test_that("private setup validates handles and escapes a mocked password", {
  local_files()
  calls <- 0L
  local_mocked_bindings(prompt_password = function() { calls <<- calls + 1L; "synthetic:with\\separator" })
  for (bad in list(NULL, "ro", "Upper", "1bad", "../bad", paste(rep("a", 49), collapse = ""))) expect_error(mdp_setup(bad), "Handle")
  expect_equal(calls, 0L)
  mdp_setup("demo")
  mdp_setup("demo")
  expect_equal(calls, 2L)
  expect_length(readLines(libpq_path(TRUE)), 1)
  expect_equal(service_config("mdp")$sslmode, "require")
  expect_false(any(grepl("synthetic", readLines(libpq_path()))))
  expect_true(has_password("mdp"))
})

test_that("sitrep returns each check without requiring a connection", {
  local_files()
  local_mocked_bindings(mdp_connect = function(...) stop("offline"), tcp_open = function(...) FALSE)
  capture.output(result <- mdp_sitrep())
  expect_equal(nrow(result), 17L)
  expect_true(all(c("check", "ok", "detail", "fix") %in% names(result)))
  expect_true(all(nzchar(result$fix)))
})

test_that("noninteractive connection refuses absent password before libpq", {
  local_files()
  expect_error(mdp_connect("mdp"), "mdp_setup")
})

test_that("rights and eligibility work on frames and lazy queries", {
  x <- rights_frame()
  expect_equal(mdp_learnable(x)$id, 1L)
  expect_identical(mdp_check_rights(x), x)
  expect_error(mdp_check_rights(data.frame()), "learning_eligible, resale_permitted, source_keys")
  expect_error(mdp_learnable(data.frame()), "learning_eligible")
  con <- DBI::dbConnect(duckdb::duckdb())
  on.exit(DBI::dbDisconnect(con, shutdown = TRUE))
  DBI::dbWriteTable(con, "rows", x)
  q <- dplyr::tbl(con, "rows")
  expect_identical(mdp_check_rights(q), q)
  expect_equal(dplyr::collect(mdp_learnable(q))$id, 1L)
  expect_error(mdp_check_rights(dplyr::select(q, id)), "learning_eligible")
})

test_that("snapshot connections are read only and panes implement the contract", {
  path <- tempfile(fileext = ".duckdb")
  on.exit(unlink(path))
  writer <- DBI::dbConnect(duckdb::duckdb(), path)
  DBI::dbExecute(writer, "CREATE SCHEMA marts")
  DBI::dbExecute(writer, "CREATE TABLE marts.example AS SELECT range AS id FROM range(2000)")
  DBI::dbDisconnect(writer, shutdown = TRUE)
  pane <- NULL
  closed <- NULL
  withr::local_options(connectionObserver = list(connectionOpened = function(...) pane <<- list(...), connectionClosed = function(...) closed <<- list(...)))
  con <- mdp_connect(path)
  on.exit(if (DBI::dbIsValid(con)) DBI::dbDisconnect(con, shutdown = TRUE), add = TRUE)
  expect_error(DBI::dbExecute(con, "CREATE TABLE forbidden (i INTEGER)"), "[Rr]ead.only")
  expect_equal(pane$type, "Music Data Platform warehouse")
  expect_true("marts" %in% pane$listObjects()$name)
  expect_equal(pane$listObjects(schema = "marts")$name, "example")
  expect_equal(pane$listColumns(schema = "marts", table = "example")$name, "id")
  expect_equal(nrow(pane$previewObject(schema = "marts", table = "example", limit = 2)), 2)
  expect_equal(nrow(pane$previewObject(schema = "marts", table = "example", limit = 5000)), 1000)
  expect_error(pane$previewObject(schema = "marts", table = "example", limit = NA), "limit")
  pane$disconnect()
  expect_equal(closed$host, path)
})

test_that("SQL from a DuckDB table uses PostgreSQL expressions and CTEs", {
  con <- DBI::dbConnect(duckdb::duckdb())
  on.exit(DBI::dbDisconnect(con, shutdown = TRUE))
  DBI::dbWriteTable(con, "rows", data.frame(id = 1:3, text = c("ab", "cd", "ef")))
  q <- dplyr::tbl(con, "rows") |>
    dplyr::mutate(bucket = as.character(id), position = row_number()) |>
    dplyr::filter(position > 1)
  capture.output(sql <- mdp_sql(q))
  expect_match(sql, "WITH")
  expect_match(sql, "AS TEXT", ignore.case = TRUE)
  file <- tempfile()
  on.exit(unlink(file), add = TRUE)
  expect_identical(mdp_sql(q, file), file)
  expect_equal(paste(readLines(file), collapse = "\n"), sql)
})

test_that("catalog reads the bundled contracts", {
  catalog <- mdp_catalog()
  expect_s3_class(catalog, "tbl_df")
  expect_true(all(c("relation", "access", "grain", "columns", "rights_columns") %in% names(catalog)))
  expect_true("marts.mart_chart_history" %in% catalog$relation)
})

test_that("sandbox guards run before any SQL", {
  expect_error(mdp_save_view(data.frame(id = 1), "example", NULL), "Missing rights")
  expect_error(mdp_write(data.frame(id = 1), "example", NULL), "Missing rights")
})

test_that("sandbox write collisions name the object and recovery", {
  con <- DBI::dbConnect(duckdb::duckdb())
  on.exit(DBI::dbDisconnect(con, shutdown = TRUE))
  DBI::dbExecute(con, "CREATE SCHEMA sandbox_demo")
  local_mocked_bindings(own_sandbox = function(con, name) DBI::Id(schema = "sandbox_demo", table = name))
  mdp_write(rights_frame(), "copy", con)
  message <- tryCatch(mdp_write(rights_frame(), "copy", con), error = conditionMessage)
  expect_match(message, "sandbox_demo.*copy")
  expect_match(message, "Choose a new name")
  expect_match(message, "overwrite = TRUE", fixed = TRUE)
  expect_no_error(mdp_write(rights_frame(), "copy", con, overwrite = TRUE))
})

test_that("pins require eligible training and upsert provenance", {
  con <- mock_warehouse_connection()
  local_mocked_bindings(dbIsValid = function(...) TRUE,
                        dbGetQuery = function(...) data.frame(failed = 0), .package = "DBI")
  withr::local_dir(withr::local_tempdir())
  board <- pins::board_folder("_pins", versioned = TRUE)
  expect_error(mdp_pin_model(list(), "fit", rights_frame(), board), "all have")
  train <- mdp_learnable(rights_frame())
  version <- mdp_pin_model(list(coefficient = 1), "fit", train, board, inputs = "marts.example", metrics = list(error = 0), con = con)
  expect_true(nzchar(version))
  expect_equal(pins::pin_read(board, "fit")$coefficient, 1)
  expect_equal(pins::pin_meta(board, "fit")$user$filter, "learning_eligible IS TRUE")
  entries <- yaml::read_yaml("models.yml")$models
  expect_length(entries, 1)
  expect_equal(entries[[1]]$rows, 1)
  mdp_pin_model(list(coefficient = 2), "fit", train, board, con = con)
  expect_length(yaml::read_yaml("models.yml")$models, 1)
})

test_that("new analysis fills a source-only template and refuses unsafe paths", {
  repo <- withr::local_tempdir()
  dir.create(file.path(repo, "r/mdpr"), recursive = TRUE)
  file.create(file.path(repo, "r/mdpr/DESCRIPTION"))
  path <- mdp_new_analysis("chart-example", "demo", repo, open = FALSE, renv = FALSE)
  expect_true(file.exists(file.path(path, "R/features.R")))
  expect_true(file.exists(file.path(path, ".gitignore")))
  expect_true(file.exists(file.path(path, ".Rprofile")))
  expect_true(any(grepl("chart-example", readLines(file.path(path, "README.md")))))
  expect_error(mdp_new_analysis("chart-example", "demo", repo, renv = FALSE), "already exists")
  expect_error(mdp_new_analysis("../escape", "demo", repo, renv = FALSE), "lowercase")
})

test_that("snapshot delegates to the one writer with quoted arguments", {
  repo <- withr::local_tempdir()
  dir.create(file.path(repo, "r/mdpr"), recursive = TRUE)
  file.create(file.path(repo, "r/mdpr/DESCRIPTION"))
  expect_error(mdp_snapshot("out.duckdb", repo = repo), "snapshot writer is missing")
  dir.create(file.path(repo, "functions/src/mdp_functions"), recursive = TRUE)
  file.create(file.path(repo, "functions/src/mdp_functions/warehouse_snapshot.py"))
  call <- NULL
  local_mocked_bindings(find_command = function(...) "uv", run_command = function(...) { call <<- list(...); 0L })
  expect_identical(mdp_snapshot("out file.duckdb", repo = repo), "out file.duckdb")
  expect_equal(call[[1]], "uv")
  expect_true(shQuote("out file.duckdb") %in% call[[2]])
  expect_match(call$env, "MDP_ANALYST_URL=.*service=mdp")
})

test_that("snapshot manifests follow collected training rows into pins after a live check", {
  con <- DBI::dbConnect(duckdb::duckdb())
  on.exit(DBI::dbDisconnect(con, shutdown = TRUE))
  DBI::dbExecute(con, "CREATE SCHEMA marts")
  DBI::dbWriteTable(con, DBI::Id(schema = "marts", table = "example"), rights_frame())
  DBI::dbWriteTable(con, "_mdp_snapshot", data.frame(close_no = 3, relation = "marts.example"))
  train <- dplyr::collect(mdp_learnable(mdp_tbl(con, "example")))
  expect_equal(attr(train, "mdp_snapshot")$close_no, 3)
  withr::local_dir(withr::local_tempdir())
  board <- pins::board_folder("_pins", versioned = TRUE)
  live <- mock_warehouse_connection()
  local_mocked_bindings(dbIsValid = function(...) TRUE,
                        dbGetQuery = function(...) data.frame(failed = 0), .package = "DBI")
  mdp_pin_model(list(), "snapshot-fit", train, board, con = live)
  expect_equal(pins::pin_meta(board, "snapshot-fit")$user$snapshot$close_no, 3)
})


test_that("snapshot failures include the child diagnostic and reconnect action", {
  repo <- withr::local_tempdir()
  dir.create(file.path(repo, "r/mdpr"), recursive = TRUE)
  file.create(file.path(repo, "r/mdpr/DESCRIPTION"))
  dir.create(file.path(repo, "functions/src/mdp_functions"), recursive = TRUE)
  file.create(file.path(repo, "functions/src/mdp_functions/warehouse_snapshot.py"))
  local_mocked_bindings(find_command = function(...) "uv", run_command = function(..., stdout, stderr) {
    structure("Reader connection refused", status = 1L)
  })
  message <- tryCatch(mdp_snapshot("out.duckdb", repo = repo), error = conditionMessage)
  expect_match(message, "Reader connection refused")
  expect_match(message, "mdp_connect")
})

test_that("local guards refuse before any warehouse read or pin write", {
  withr::local_dir(withr::local_tempdir())
  local_mocked_bindings(dbGetQuery = function(...) stop("Unexpected query"), .package = "DBI")
  train <- rights_frame()
  expect_error(mdp_pin_model(list(), "fit", train), "all have")
  train <- mdp_learnable(train)
  expect_error(mdp_pin_model(list(), "fit", train[, -4]), "Missing rights")
  for (bad in c(NA_character_, "[]", "{}", "null", '[null]', '[1]', '[""]', '[" "]',
                '[{"secret":"value"}]', '"eligible"', "private-invalid-json")) {
    train$source_keys <- bad
    err <- tryCatch(mdp_pin_model(list(), "fit", train), error = identity)
    expect_s3_class(err, "r_pin_sources_invalid")
    expect_match(conditionMessage(err), "mdp_tbl", fixed = TRUE)
    expect_false(grepl("private|secret", conditionMessage(err)))
  }
  expect_error(mdp_pin_model(list(), "fit", train[0, ]), class = "r_pin_sources_invalid")
  expect_false(dir.exists("_pins"))
  expect_false(file.exists("models.yml"))
})

test_that("pin keys are distinct across joined rows and arrays", {
  train <- data.frame(source_keys = c('["one", "two", "one"]', '["two", "three"]'))
  expect_identical(pin_source_keys(train), c("one", "two", "three"))
})

test_that("offline, closed and snapshot connections cannot authorize pins", {
  withr::local_dir(withr::local_tempdir())
  train <- mdp_learnable(rights_frame())
  snapshot <- DBI::dbConnect(duckdb::duckdb())
  on.exit(DBI::dbDisconnect(snapshot, shutdown = TRUE))
  local_mocked_bindings(dbIsValid = function(...) FALSE, .package = "DBI")
  for (con in list(NULL, snapshot, mock_warehouse_connection())) {
    err <- tryCatch(mdp_pin_model(list(), "fit", train, con = con), error = identity)
    expect_s3_class(err, "r_pin_connection_required")
    expect_match(conditionMessage(err), "mdp_connect", fixed = TRUE)
  }
  expect_false(dir.exists("_pins"))
  expect_false(file.exists("models.yml"))
})

test_that("warehouse refusal reports only the failed key count and recovery", {
  withr::local_dir(withr::local_tempdir())
  con <- mock_warehouse_connection()
  local_mocked_bindings(dbIsValid = function(...) TRUE, .package = "DBI")
  calls <- 0L
  local_mocked_bindings(dbGetQuery = function(conn, statement, params) {
    calls <<- calls + 1L
    expect_false(grepl("private", statement))
    expect_identical(jsonlite::fromJSON(params[[1]]), c("eligible", "private-denied", "private-unknown"))
    data.frame(failed = 2)
  }, .package = "DBI")
  train <- rights_frame()
  train$learning_eligible <- TRUE
  train$source_keys <- c('["eligible", "private-denied"]', '["private-unknown"]', '["private-denied"]')
  err <- tryCatch(mdp_pin_model(list(), "fit", train, con = con), error = identity)
  expect_s3_class(err, "r_pin_rights_refused")
  expect_match(conditionMessage(err), "2 source keys")
  expect_match(conditionMessage(err), "refit")
  expect_false(grepl("private", conditionMessage(err)))
  expect_equal(calls, 1L)
  expect_false(dir.exists("_pins"))
  expect_false(file.exists("models.yml"))
})

test_that("warehouse failures refuse without exposing database diagnostics", {
  con <- mock_warehouse_connection()
  local_mocked_bindings(dbIsValid = function(...) TRUE,
                        dbGetQuery = function(...) stop("private-query-value"), .package = "DBI")
  err <- tryCatch(check_pin_warehouse(con, "eligible"), error = identity)
  expect_s3_class(err, "r_pin_rights_unavailable")
  expect_match(conditionMessage(err), "mdp_connect", fixed = TRUE)
  expect_false(grepl("private", conditionMessage(err)))
})

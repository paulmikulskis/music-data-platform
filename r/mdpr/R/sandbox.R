find_command <- function(command) Sys.which(command)
run_command <- function(...) system2(...)

sandbox_policy <- function() jsonlite::fromJSON(system.file("extdata", "contracts.json", package = "mdpr"))$sandbox
sandbox_message <- function(code) sandbox_policy()$messages[[code]]

sandbox_write_error <- function(error, id) {
  stop(paste(as.character(id), conditionMessage(error), sandbox_message("sandbox_write_failed")), call. = FALSE)
}

sandbox_query <- function() {
  "SELECT schema_name FROM catalog.sandbox_status WHERE owner_role=current_user ORDER BY schema_name"
}

own_sandbox <- function(con, name) {
  if (length(name) != 1L || is.na(name) || !grepl(sandbox_policy()$object_pattern, name)) stop(sandbox_message("sandbox_object_invalid"))
  rows <- DBI::dbGetQuery(con, sandbox_query())[[1]]
  if (length(rows) != 1L) stop(sandbox_message("sandbox_missing"), call. = FALSE)
  DBI::Id(schema = rows[[1]], table = name)
}

#' Save a query as a view in your sandbox
#' @param x Lazy query retaining rights columns.
#' @param name Lowercase SQL identifier.
#' @param con PostgreSQL connection.
#' @return The database execution result, invisibly.
#' @export
mdp_save_view <- function(x, name, con) {
  mdp_check_rights(x)
  id <- DBI::dbQuoteIdentifier(con, own_sandbox(con, name))
  sql <- dbplyr::sql_render(x, con = dbplyr::simulate_postgres(), sql_options = dbplyr::sql_options(cte = TRUE))
  invisible(tryCatch(DBI::dbExecute(con, paste("CREATE OR REPLACE VIEW", id, "AS", sql)),
                     error = function(error) sandbox_write_error(error, id)))
}

#' Write a frame to your sandbox
#' @param df Data frame retaining rights columns.
#' @param name Lowercase SQL identifier.
#' @param con PostgreSQL connection.
#' @param overwrite Whether to replace an existing table.
#' @return The DBI write result, invisibly.
#' @export
mdp_write <- function(df, name, con, overwrite = FALSE) {
  mdp_check_rights(df)
  id <- own_sandbox(con, name)
  invisible(tryCatch(DBI::dbWriteTable(con, id, df, overwrite = overwrite),
                     error = function(error) sandbox_write_error(error, DBI::dbQuoteIdentifier(con, id))))
}

#' Launch the warehouse snapshot writer
#' @param out Destination DuckDB path.
#' @param schemas Schemas to include.
#' @param db libpq service name.
#' @param repo Optional repository root.
#' @return out invisibly.
#' @export
mdp_snapshot <- function(out, schemas = c("marts", "intermediate", "staging"), db = "mdp", repo = NULL) {
  repo <- find_repo(repo)
  if (!file.exists(file.path(repo, "functions/src/mdp_functions/warehouse_snapshot.py"))) stop("The snapshot writer is missing. Pull the latest full checkout and rerun mdp_snapshot().")
  if (!nzchar(find_command("uv"))) stop("Install uv to create snapshots.")
  args <- c("run", "--project", file.path(repo, "functions"), "python", "-m", "mdp_functions.warehouse_snapshot",
            "--schemas", paste(schemas, collapse = ","), "--out", out)
  status <- run_command("uv", vapply(args, shQuote, character(1)), env = paste0("MDP_ANALYST_URL=", shQuote(paste0("service=", db))), stdout = TRUE, stderr = TRUE)
  exit <- if (is.numeric(status)) status else attr(status, "status")
  if (!is.null(exit) && exit != 0L) stop(paste(c(utils::tail(as.character(status), 8), sandbox_message("sandbox_snapshot_failed")), collapse = "\n"), call. = FALSE)
  invisible(out)
}

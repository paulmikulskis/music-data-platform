#' Open a lazy warehouse relation
#' @param con A DBI connection.
#' @param name Relation name.
#' @param schema Warehouse schema.
#' @return A lazy dbplyr table.
#' @export
mdp_tbl <- function(con, name, schema = "marts") {
  # DuckDB's tbl method does not accept dbplyr in_schema objects; src_dbi
  # retains its SQL dialect while routing qualified names through dbplyr.
  source <- if (inherits(con, "duckdb_connection")) dbplyr::src_dbi(con) else con
  x <- tryCatch(dplyr::tbl(source, dbplyr::in_schema(schema, name)), error = function(error) {
    detail <- conditionMessage(error)
    code <- if (schema == "raw" && grepl("permission denied", detail)) "raw_read_denied" else
      if (grepl("^(explore_)?tenant_", schema) && grepl("does not exist|permission denied", detail)) "tenant_read_denied" else
      if (grepl("does not exist", detail)) "model_not_built" else "workbench_query_failed"
    hint <- error_entry(code)
    if (code == "tenant_read_denied") rlang::abort(paste(hint$summary, hint$next_step), class = code)
    rlang::abort(paste(hint$summary, detail, hint$next_step), class = code, parent = error)
  })
  if (inherits(con, "duckdb_connection") && DBI::dbExistsTable(con, "_mdp_snapshot")) {
    class(x) <- c("mdp_snapshot_tbl", class(x))
  }
  x
}

#' Read the live or bundled contract catalog
#' @param con Optional DBI connection.
#' @return A tibble with relation, access, grain, columns and rights columns.
#' @export
mdp_catalog <- function(con = NULL) {
  if (inherits(con, "PqConnection")) {
    present <- DBI::dbGetQuery(con, "SELECT to_regclass('catalog.relations')")[[1]]
    if (!is.na(present)) return(dplyr::as_tibble(DBI::dbGetQuery(con, "SELECT * FROM catalog.relations")))
  }
  path <- system.file("extdata/contracts.json", package = "mdpr", mustWork = TRUE)
  dplyr::as_tibble(jsonlite::fromJSON(path)$relations)
}

#' Render a lazy query as PostgreSQL SQL
#' @param x Lazy dbplyr query, including one backed by a snapshot.
#' @param file Optional output file.
#' @return The SQL invisibly, or the output path invisibly when file is supplied.
#' @export
mdp_sql <- function(x, file = NULL) {
  sql <- as.character(dbplyr::sql_render(x, con = dbplyr::simulate_postgres(),
                                       sql_options = dbplyr::sql_options(cte = TRUE)))
  if (is.null(file)) {
    cat(sql, "\n", sep = "")
    return(invisible(sql))
  }
  writeLines(sql, file)
  invisible(file)
}

#' @importFrom dplyr collect
#' @export
collect.mdp_snapshot_tbl <- function(x, ...) {
  manifest <- DBI::dbReadTable(dbplyr::remote_con(x), "_mdp_snapshot")
  result <- NextMethod()
  attr(result, "mdp_snapshot") <- manifest
  result
}

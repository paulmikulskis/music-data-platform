#' Connect to a warehouse service or a read-only snapshot
#' @param db A libpq service name or a path ending in .duckdb.
#' @return A DBI connection. Close with DBI::dbDisconnect() or the Connections pane.
#' @export
mdp_connect <- function(db = Sys.getenv("MDP_DB", "mdp")) {
  if (grepl("\\.duckdb$", db)) {
    if (!requireNamespace("duckdb", quietly = TRUE)) stop("Missing duckdb. Run install.packages('duckdb') to open snapshots.")
    con <- DBI::dbConnect(duckdb::duckdb(), dbdir = db, read_only = TRUE)
  } else {
    if (!interactive() && !has_password(db)) {
      stop('Missing password-file entry. Run:\npak::local_install("r/mdpr")\nmdpr::mdp_setup(',
           if (db == "mdp_local") 'target = "local"' else '"<handle>"', ')', call. = FALSE)
    }
    con <- DBI::dbConnect(RPostgres::Postgres(), service = db, bigint = "numeric",
                          timezone = "UTC", timezone_out = "UTC", check_interrupts = TRUE,
                          application_name = "mdpr")
  }
  register_pane(con, db)
  con
}

register_pane <- function(con, db) {
  observer <- getOption("connectionObserver")
  if (is.null(observer)) return(invisible(con))
  quote <- function(x) as.character(DBI::dbQuoteString(con, x))
  object <- function(schema, table = NULL, view = NULL) {
    DBI::dbQuoteIdentifier(con, DBI::Id(schema = schema, table = if (is.null(table)) view else table))
  }
  closed <- FALSE
  notify_closed <- function() {
    if (!closed) observer$connectionClosed(type = "Music Data Platform warehouse", host = db)
    closed <<- TRUE
  }
  # Direct DBI disconnects are observed when control returns to the R console.
  hook <- addTaskCallback(function(...) {
    if (DBI::dbIsValid(con)) return(TRUE)
    notify_closed()
    FALSE
  })
  disconnect <- function() {
    if (DBI::dbIsValid(con)) DBI::dbDisconnect(con)
    removeTaskCallback(hook)
    notify_closed()
    invisible(NULL)
  }
  observer$connectionOpened(
    type = "Music Data Platform warehouse", host = db, displayName = paste("Music Data Platform warehouse", db),
    connectCode = sprintf("con <- mdpr::mdp_connect(%s)", encodeString(db, quote = '"')),
    disconnect = disconnect,
    listObjectTypes = function() list(schema = list(contains = list(table = list(contains = "data"), view = list(contains = "data")))),
    listObjects = function(schema = NULL, ...) {
      if (is.null(schema)) {
        rows <- DBI::dbGetQuery(con, "SELECT schema_name AS name, schema_owner FROM information_schema.schemata ORDER BY schema_name")
        own <- if (inherits(con, "PqConnection")) DBI::dbGetQuery(con, "SELECT current_user")[[1]] else ""
        keep <- rows$name %in% c("marts", "intermediate", "staging", "catalog") |
          (startsWith(rows$name, "sandbox_") & rows$schema_owner == own)
        return(data.frame(name = rows$name[keep], type = rep("schema", sum(keep))))
      }
      DBI::dbGetQuery(con, paste0("SELECT table_name AS name, CASE WHEN table_type = 'VIEW' THEN 'view' ELSE 'table' END AS type FROM information_schema.tables WHERE table_schema = ", quote(schema), " ORDER BY table_name"))
    },
    listColumns = function(schema, table = NULL, view = NULL, ...) {
      DBI::dbGetQuery(con, paste0("SELECT column_name AS name, data_type AS type FROM information_schema.columns WHERE table_schema = ", quote(schema), " AND table_name = ", quote(if (is.null(table)) view else table), " ORDER BY ordinal_position"))
    },
    previewObject = function(schema, table = NULL, view = NULL, limit = 1000L, ...) {
      if (length(limit) != 1L || !is.finite(limit) || limit < 0) stop("Invalid preview limit. Use one finite, nonnegative number.")
      DBI::dbGetQuery(con, paste("SELECT * FROM", object(schema, table, view), "LIMIT", as.integer(min(limit, 1000L))))
    },
    actions = list(Catalog = list(icon = "", callback = function() utils::View(mdp_catalog(con)))),
    connectionObject = con
  )
  invisible(con)
}

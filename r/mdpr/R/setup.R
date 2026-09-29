libpq_path <- function(password = FALSE) {
  variable <- if (password) "PGPASSFILE" else "PGSERVICEFILE"
  override <- Sys.getenv(variable)
  if (nzchar(override)) return(path.expand(override))
  if (.Platform$OS.type == "windows") {
    return(file.path(Sys.getenv("APPDATA"), "postgresql",
                     if (password) "pgpass.conf" else ".pg_service.conf"))
  }
  path.expand(if (password) "~/.pgpass" else "~/.pg_service.conf")
}

read_lines <- function(path) {
  if (file.exists(path)) readLines(path, warn = FALSE) else character()
}

validate_handle <- function(handle) {
  if (length(handle) != 1L || is.na(handle) ||
      !grepl("^[a-z][a-z0-9_]{0,47}$", handle) || handle == "ro") {
    stop("Handle is invalid; use 1-48 lowercase letters, digits or underscores, starting with a letter; do not use ro.", call. = FALSE)
  }
  handle
}

service_config <- function(db) {
  lines <- read_lines(libpq_path())
  headers <- which(grepl("^\\s*\\[.*\\]\\s*$", lines))
  match <- which(trimws(lines[headers]) == paste0("[", db, "]"))
  if (!length(match)) return(list())
  start <- headers[match[1]]
  end <- c(headers[headers > start], length(lines) + 1L)[1] - 1L
  block <- if (end > start) lines[seq.int(start + 1L, end)] else character()
  block <- block[grepl("^[a-zA-Z_]+\\s*=", block)]
  stats::setNames(as.list(trimws(sub("^[^=]*=", "", block))), trimws(sub("=.*", "", block)))
}

pass_prefix <- function(config) {
  paste(config$host, config$port, config$dbname, config$user, sep = ":")
}

has_password <- function(db) {
  config <- service_config(db)
  if (!all(c("host", "port", "dbname", "user") %in% names(config))) return(FALSE)
  # Match literal entries and libpq wildcards without retaining or displaying secrets.
  any(vapply(strsplit(read_lines(libpq_path(TRUE)), ":", fixed = TRUE), function(parts) {
    length(parts) >= 5L && all(parts[1:4] == "*" | parts[1:4] == unlist(config[c("host", "port", "dbname", "user")]))
  }, logical(1)))
}

prompt_password <- function() {
  if (requireNamespace("rstudioapi", quietly = TRUE) && rstudioapi::isAvailable()) {
    value <- rstudioapi::askForPassword("Warehouse password")
  } else if (requireNamespace("askpass", quietly = TRUE)) {
    value <- askpass::askpass("Warehouse password")
  } else stop("Install askpass or run mdp_setup() in an IDE with a masked password prompt.")
  if (is.null(value) || !nzchar(value) || grepl("[\r\n]", value)) stop("Password entry cancelled or invalid. Run mdp_setup() to try again.")
  value
}

write_private <- function(lines, path) {
  dir.create(dirname(path), recursive = TRUE, showWarnings = FALSE)
  if (!file.exists(path)) file.create(path)
  Sys.chmod(path, "0600")
  writeLines(lines, path, useBytes = TRUE)
}

#' Configure warehouse service and password files
#' @param handle Lowercase analyst handle; required for the private warehouse.
#' @param target Private warehouse or disposable local stack.
#' @param port Optional proxy or local port override.
#' @return The service name, invisibly.
#' @export
mdp_setup <- function(handle = NULL, target = c("fly", "local"), port = NULL) {
  target <- match.arg(target)
  local <- target == "local"
  handle <- if (local) "local" else validate_handle(handle)
  port <- if (is.null(port)) { if (local) 56432L else 15472L } else port
  if (length(port) != 1L || is.na(port) || !grepl("^[0-9]+$", as.character(port)) || port < 1 || port > 65535) cli::cli_abort("Invalid TCP port. Use a number from 1 to 65535.")
  db <- if (local) "mdp_local" else "mdp"
  config <- list(host = "127.0.0.1", port = as.character(port), dbname = "warehouse",
                 user = paste0("analyst_", handle), sslmode = if (local) "prefer" else "require")
  password <- if (local) "analyst_local" else prompt_password()
  lines <- read_lines(libpq_path())
  sections <- cumsum(grepl("^\\s*\\[.*\\]\\s*$", lines))
  remove <- sections[trimws(lines) == paste0("[", db, "]")]
  lines <- lines[!sections %in% remove]
  block <- c(paste0("[", db, "]"), paste0(names(config), "=", unlist(config)))
  write_private(c(lines, block), libpq_path())
  prefix <- paste0(pass_prefix(config), ":")
  passwords <- read_lines(libpq_path(TRUE))
  passwords <- passwords[!startsWith(passwords, prefix)]
  password <- gsub("\\", "\\\\", password, fixed = TRUE)
  password <- gsub(":", "\\:", password, fixed = TRUE)
  # Prepend so an existing wildcard entry cannot override the new credential.
  write_private(c(paste0(prefix, password), passwords), libpq_path(TRUE))
  invisible(db)
}

find_repo <- function(repo = NULL) {
  path <- normalizePath(if (is.null(repo)) getwd() else repo, mustWork = TRUE)
  repeat {
    if (file.exists(file.path(path, "r/mdpr/DESCRIPTION"))) return(path)
    parent <- dirname(path)
    if (parent == path) stop("Repository not found. Run from the clone or supply repo.", call. = FALSE)
    path <- parent
  }
}

find_flyctl <- function() {
  candidates <- c(Sys.getenv("MDP_FLYCTL"), Sys.which(c("flyctl", "fly")),
                  path.expand("~/.fly/bin/flyctl"), path.expand("~/.fly/bin/fly"),
                  "/opt/homebrew/bin/flyctl", "/usr/local/bin/flyctl")
  candidates[file.exists(candidates) & file.access(candidates, 1) == 0][1]
}

tcp_open <- function(config) {
  if (is.null(config$port)) return(FALSE)
  tryCatch({
    socket <- socketConnection(config$host, as.integer(config$port), open = "r+", timeout = 1)
    close(socket)
    TRUE
  }, error = function(e) FALSE, warning = function(w) FALSE)
}

#' Diagnose an analyst environment
#' @return A data frame of check, ok, detail and fix columns, invisibly.
#' @export
mdp_sitrep <- function() {
  db <- Sys.getenv("MDP_DB", "mdp")
  config <- service_config(db)
  results <- list()
  add <- function(check, ok, detail = "", fix) {
    results[[length(results) + 1L]] <<- data.frame(check, ok = isTRUE(ok), detail, fix)
  }
  setup <- if (db == "mdp_local") 'mdpr::mdp_setup(target = "local")' else 'mdpr::mdp_setup("<handle>")'
  add("R version", getRversion() >= "4.5", as.character(getRversion()), 'Install R >= 4.5: brew install --cask r (macOS), winget install RProject.R (Windows)')
  repo <- tryCatch(find_repo(), error = function(e) NA_character_)
  matches <- !is.na(repo) && as.character(utils::packageVersion("mdpr")) == read.dcf(file.path(repo, "r/mdpr/DESCRIPTION"))[1, "Version"]
  add("mdpr version", matches, fix = 'pak::local_install("r/mdpr")')
  add("service file", file.exists(libpq_path()), libpq_path(), setup)
  add("service block", length(config) > 0L, db, setup)
  add("password entry", has_password(db), libpq_path(TRUE), setup)
  mode <- file.info(libpq_path(TRUE))$mode
  add("password mode", .Platform$OS.type == "windows" || (!is.na(mode) && as.integer(mode) == 384L), fix = paste0('Sys.chmod("', libpq_path(TRUE), '", "0600")'))
  add("TCP port", tcp_open(config), fix = if (db == "mdp_local") "bash ops/local/up.sh" else "fly proxy 15472:5432 -a mdp-postgres --bind-addr 127.0.0.1")
  con <- tryCatch(mdp_connect(db), error = function(e) NULL)
  if (!is.null(con)) on.exit(DBI::dbDisconnect(con), add = TRUE)
  add("login", !is.null(con), fix = setup)
  query <- function(sql) if (is.null(con)) NULL else tryCatch(DBI::dbGetQuery(con, sql)[[1]], error = function(e) NULL)
  for (setting in c("current_user", "search_path", "statement_timeout")) {
    value <- query(if (setting == "current_user") "SELECT current_user" else paste("SHOW", setting))
    ok <- length(value) == 1L && switch(setting, current_user = startsWith(value, "analyst_"), search_path = grepl("marts", value), statement_timeout = value %in% c("30s", "30000"))
    add(setting, ok, paste(value, collapse = ", "), "Ask the operator to reconcile analyst grants and role settings")
  }
  add("own sandbox", length(query(sandbox_query())) == 1L, fix = "Ask the operator to deploy sandbox grants")
  add("catalog.relations", length(query("SELECT to_regclass('catalog.relations')")) == 1L && !is.na(query("SELECT to_regclass('catalog.relations')")), fix = "Use mdpr::mdp_catalog(); ask the operator to deploy the catalog")
  add("flyctl", !is.na(find_flyctl()), fix = "MDP_FLYCTL=<path> in ~/.Renviron")
  fixes <- c(git = "brew install git (macOS); sudo apt-get install git (Linux); winget install Git.Git (Windows)",
             uv = "brew install uv (macOS); pipx install uv (Linux); winget install astral-sh.uv (Windows)",
             pnpm = "corepack enable && corepack prepare pnpm@10 --activate")
  for (tool in names(fixes)) add(tool, nzchar(Sys.which(tool)), fix = unname(fixes[[tool]]))
  result <- do.call(rbind, results)
  for (i in seq_len(nrow(result))) cat(if (result$ok[i]) "PASS" else "FAIL", result$check[i], ":", if (result$ok[i]) result$detail[i] else result$fix[i], "\n")
  invisible(result)
}

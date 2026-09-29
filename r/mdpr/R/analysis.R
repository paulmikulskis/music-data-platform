#' Create a reproducible analysis folder
#' @param topic Folder name using lowercase letters, digits and hyphens.
#' @param handle Analyst handle; defaults to the configured service user.
#' @param repo Optional repository root.
#' @param open Open the project in an available IDE.
#' @param renv Initialize and populate an isolated renv library.
#' @return The analysis path invisibly.
#' @export
mdp_new_analysis <- function(topic, handle = NULL, repo = NULL, open = interactive(), renv = TRUE) {
  repo <- find_repo(repo)
  if (is.null(handle)) handle <- sub("^analyst_", "", service_config(Sys.getenv("MDP_DB", "mdp"))$user)
  handle <- validate_handle(handle)
  if (length(topic) != 1L || is.na(topic) || !grepl("^[a-z][a-z0-9-]*$", topic)) stop("Invalid topic. Use lowercase letters, digits and hyphens.")
  path <- file.path(repo, "analyses", handle, topic)
  if (file.exists(path)) stop("Analysis folder already exists: ", path, ". Open it or choose another topic.")
  template <- system.file("templates/analysis", package = "mdpr", mustWork = TRUE)
  dir.create(path, recursive = TRUE)
  files <- list.files(template, all.files = TRUE, recursive = TRUE, include.dirs = FALSE, no.. = TRUE)
  for (name in files) {
    destination <- file.path(path, if (name %in% c("gitignore", "Rprofile")) paste0(".", name) else name)
    dir.create(dirname(destination), recursive = TRUE, showWarnings = FALSE)
    text <- readLines(file.path(template, name), warn = FALSE)
    writeLines(gsub("{{handle}}", handle, gsub("{{topic}}", topic, text, fixed = TRUE), fixed = TRUE), destination)
  }
  if (renv) {
    if (!requireNamespace("renv", quietly = TRUE)) stop("Install renv before initializing the analysis library.")
    renv::init(project = path, bare = TRUE, restart = FALSE)
    renv::settings$ignored.packages(c("mdpr", "IRkernel", "languageserver"), project = path)
    packages <- c("DBI", "RPostgres", "dplyr", "dbplyr", "duckdb", "jsonlite", "testthat", "pins", "yaml", "targets", "tarchetypes", "knitr", "rmarkdown")
    renv::install(packages, project = path, prompt = FALSE)
    renv::install(file.path(repo, "r/mdpr"), project = path, prompt = FALSE)
    renv::snapshot(project = path, type = "all", prompt = FALSE)
  }
  if (open && requireNamespace("rstudioapi", quietly = TRUE) && rstudioapi::isAvailable()) rstudioapi::openProject(path)
  invisible(path)
}

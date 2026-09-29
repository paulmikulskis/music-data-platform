args <- commandArgs(trailingOnly = TRUE)
stopifnot(length(args) == 1L)
repo <- normalizePath(".")
setwd(args[[1]])
if (file.exists("renv.lock")) {
  # Select the project library before checking which packages need restoring.
  renv::load()
  renv::restore(prompt = FALSE)
  renv::install(file.path(repo, "r/mdpr"), prompt = FALSE)
}
for (file in list.files("R", pattern = "\\.[Rr]$", recursive = TRUE, full.names = TRUE)) parse(file)
for (file in list.files(pattern = "\\.qmd$", recursive = TRUE)) {
  out <- tempfile(fileext = ".R")
  knitr::purl(file, output = out, quiet = TRUE)
  parse(out)
  unlink(out)
}
if (dir.exists("tests/testthat")) testthat::test_dir("tests/testthat", stop_on_failure = TRUE)
if (file.exists("_targets.R")) withCallingHandlers(targets::tar_validate(), warning = function(w) stop(w))
cat("PASS analysis", args[[1]], "\n")

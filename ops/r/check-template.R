# An isolated repository skeleton keeps scaffolded files outside the checkout.
root <- "/tmp/mdp-w-r/template"
if (dir.exists(root)) unlink(root, recursive = TRUE)
dir.create(file.path(root, "r"), recursive = TRUE)
file.symlink("/work/r/mdpr", file.path(root, "r/mdpr"))
path <- mdpr::mdp_new_analysis("template", "local", repo = root, open = FALSE, renv = TRUE)
lock <- jsonlite::fromJSON(file.path(path, "renv.lock"))
stopifnot(!any(c("mdpr", "IRkernel", "languageserver") %in% names(lock$Packages)))
cat("PASS template lock excludes local and IDE packages\n")
setwd(path)
Sys.unsetenv("MDP_DB")
testthat::test_dir("tests/testthat", stop_on_failure = TRUE)

withCallingHandlers(targets::tar_validate(), warning = function(w) stop(w))

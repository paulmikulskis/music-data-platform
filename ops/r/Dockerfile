FROM rocker/r-ver:4.6.1
RUN apt-get update && apt-get install -y --no-install-recommends libpq5 libpq-dev libcurl4-openssl-dev libssl-dev libxml2-dev libicu-dev libuv1t64 libglpk40 libgit2-1.7 git curl && apt-get clean
ENV RENV_PATHS_CACHE=/opt/renv/cache
RUN echo 'options(repos = c(CRAN = "https://p3m.dev/cran/__linux__/noble/2026-09-24"))' >> /usr/local/lib/R/etc/Rprofile.site
RUN Rscript -e 'p <- c("DBI", "RPostgres", "dplyr", "dbplyr", "jsonlite", "cli", "rlang", "duckdb", "pins", "yaml", "renv", "rstudioapi", "askpass", "usethis", "testthat", "withr", "roxygen2", "rcmdcheck", "targets", "tarchetypes", "knitr", "rmarkdown"); install.packages(p); stopifnot(all(vapply(p, requireNamespace, logical(1), quietly = FALSE)))'
WORKDIR /work

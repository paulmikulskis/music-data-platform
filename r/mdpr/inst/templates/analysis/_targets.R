# Optional pipeline: delete this file if unused. Report rendering needs Quarto.
library(targets)
source("R/features.R")
list(
  tar_target(feature_rows, {
    con <- mdpr::mdp_connect()
    tryCatch(dplyr::collect(features(con)), finally = DBI::dbDisconnect(con))
  }),
  tar_target(train, mdpr::mdp_learnable(feature_rows)),
  tar_target(fit, stats::glm(top_ten ~ chart_position, data = train, family = stats::binomial())),
  tar_target(pin, {
    con <- mdpr::mdp_connect("mdp")
    tryCatch(mdpr::mdp_pin_model(fit, "chart-example", train,
                               inputs = "marts.mart_chart_history", con = con),
             finally = DBI::dbDisconnect(con))
  }),
  tar_target(report, {
    pin
    status <- system2("quarto", c("render", "explore.qmd", "-P",
                                 shQuote(paste0("db:", Sys.getenv("MDP_DB", "mdp_local")))))
    if (status != 0L) stop("Report rendering failed; install Quarto and check the report output.")
    "explore.html"
  }, format = "file")
)

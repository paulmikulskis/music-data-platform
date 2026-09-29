# Keep the chart/week/position grain and every rights annotation.
features <- function(con) {
  mdpr::mdp_tbl(con, "mart_chart_history") |>
    dplyr::select(dplyr::all_of(c("chart_name", "chart_week", "chart_position",
                                "learning_eligible", "resale_permitted", "source_keys"))) |>
    dplyr::mutate(top_ten = .data$chart_position <= 10L)
}

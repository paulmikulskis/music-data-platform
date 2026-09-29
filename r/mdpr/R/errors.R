error_entry <- function(error_class) {
  catalog <- jsonlite::fromJSON(system.file("extdata", "error_catalog.json", package = "mdpr"))
  hint <- if (length(error_class) == 1L && !is.na(error_class)) catalog[[error_class]] else NULL
  if (is.null(hint)) hint <- catalog$unmapped
  hint
}

#' Find the next step for a platform error
#' @param error_class Error code from a run or API response.
#' @return A next-step string. Unknown codes use the shared fallback.
#' @export
mdp_error_hint <- function(error_class) error_entry(error_class)$next_step

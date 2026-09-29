#' Keep only rows eligible for derivative learning
#' @param x A data frame or lazy table with learning_eligible.
#' @return The filtered input.
#' @importFrom rlang .data
#' @export
mdp_learnable <- function(x) {
  if (!"learning_eligible" %in% colnames(x)) stop("Missing rights column: learning_eligible. Select it from the mart, then filter with mdp_learnable(x).", call. = FALSE)
  dplyr::filter(x, .data$learning_eligible == TRUE)
}

#' Require the three serving rights columns
#'
#' This checks column presence only. Use mdp_pin_model() to check current source rights.
#' @param x A data frame or lazy table.
#' @return x invisibly, or an error naming every missing column.
#' @export
mdp_check_rights <- function(x) {
  missing <- setdiff(c("learning_eligible", "resale_permitted", "source_keys"), colnames(x))
  if (length(missing)) stop("Missing rights columns: ", paste(missing, collapse = ", "), ". Select these columns from the mart before continuing.", call. = FALSE)
  invisible(x)
}

pin_rights_error <- function(code, message) {
  rlang::abort(paste(message, mdp_error_hint(code)), class = code)
}

pin_source_keys <- function(train) {
  invalid <- function() pin_rights_error("r_pin_sources_invalid", "Training source keys are missing or invalid.")
  if (!nrow(train) || !is.character(train$source_keys) || anyNA(train$source_keys)) invalid()
  arrays <- lapply(unique(train$source_keys), function(value) {
    if (!grepl("^\\s*\\[", value)) invalid()
    keys <- tryCatch(jsonlite::fromJSON(value, simplifyVector = FALSE), error = function(e) NULL)
    if (!is.list(keys) || !length(keys) ||
        !all(vapply(keys, function(key) is.character(key) && length(key) == 1L && nzchar(trimws(key)), logical(1)))) invalid()
    unlist(keys, use.names = FALSE)
  })
  unique(unlist(arrays, use.names = FALSE))
}

# The round trip checks the source registry only: each distinct source key must be learning-eligible
# in catalog.learning_rights now. It does not re-derive the row-level eligibility AND that
# mdp_annotate() computed for each training row (docs/architecture.md, "Rights annotation and
# learning_gate"); the caller's learning_eligible filter above is what covers the rows.
check_pin_warehouse <- function(con, keys) {
  if (!inherits(con, "PqConnection") || !isTRUE(tryCatch(DBI::dbIsValid(con), error = function(e) FALSE))) {
    pin_rights_error("r_pin_connection_required", "Pinning needs an open warehouse connection.")
  }
  # Bind one JSON array; source values never enter SQL or error messages.
  failed <- tryCatch(DBI::dbGetQuery(con, paste(
    "SELECT count(*) AS failed FROM jsonb_array_elements_text($1::jsonb) AS k(source_key)",
    "LEFT JOIN catalog.learning_rights r USING (source_key)",
    "WHERE r.learning_eligible IS NOT TRUE"
  ), params = list(as.character(jsonlite::toJSON(keys))))$failed,
  error = function(e) pin_rights_error("r_pin_rights_unavailable", "The warehouse rights check failed."))
  if (failed > 0) pin_rights_error("r_pin_rights_refused", sprintf("%d source keys are ineligible or unknown.", failed))
}

#' Pin a model trained only on eligible rows
#'
#' Checks local rights columns, then checks every distinct source key against the
#' current warehouse registry before writing. Missing or ineligible keys are refused.
#' Keep source_keys intact; this check cannot recover removed or replaced lineage.
#' Offline and snapshot connections cannot authorize a pin. Reconnect with mdp_connect().
#' @param fit Fitted model object.
#' @param name Pin name.
#' @param train Collected training data with all three rights columns.
#' @param con Open PostgreSQL warehouse connection from mdp_connect().
#' @param board Versioned pins board; defaults to the local ignored _pins folder.
#' @param inputs Input relation names or other provenance.
#' @param metrics Optional evaluation metrics.
#' @return The pin version; updates models.yml in the working directory.
#' @export
mdp_pin_model <- function(fit, name, train, board = pins::board_folder("_pins", versioned = TRUE), inputs = NULL, metrics = NULL, con = NULL) {
  if (!is.data.frame(train) || !"learning_eligible" %in% colnames(train) ||
      anyNA(train$learning_eligible) || !is.logical(train$learning_eligible) || !all(train$learning_eligible)) {
    stop("Training rows must all have learning_eligible TRUE. Select learning_eligible from the mart, then filter with x <- mdp_learnable(x).", call. = FALSE)
  }
  mdp_check_rights(train)
  keys <- pin_source_keys(train)
  check_pin_warehouse(con, keys)
  if (!requireNamespace("pins", quietly = TRUE) || !requireNamespace("yaml", quietly = TRUE)) stop("Install pins and yaml.")
  sha <- tryCatch(suppressWarnings(system2("git", c("rev-parse", "HEAD"), stdout = TRUE, stderr = FALSE)), error = function(e) NA_character_)
  if (!length(sha) || !is.null(attr(sha, "status"))) sha <- NA_character_
  metadata <- list(inputs = inputs, rows = nrow(train), filter = "learning_eligible IS TRUE", git_sha = sha,
                   renv_md5 = if (file.exists("renv.lock")) unname(tools::md5sum("renv.lock")) else NA_character_,
                   r_version = as.character(getRversion()), snapshot = attr(train, "mdp_snapshot"))
  pins::pin_write(board, fit, name = name, type = "rds", metadata = metadata)
  meta <- pins::pin_meta(board, name)
  entry <- list(name = name, version = meta$local$version, hash = meta$pin_hash,
                created = as.character(meta$created), inputs = inputs, rows = nrow(train), metrics = metrics)
  entries <- if (file.exists("models.yml")) yaml::read_yaml("models.yml")$models else list()
  entries <- Filter(function(x) !identical(x$name, name), entries)
  yaml::write_yaml(list(models = c(entries, list(entry))), "models.yml")
  meta$local$version
}

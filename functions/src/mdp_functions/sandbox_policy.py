"""Names, limits and next steps shared by every sandbox client."""

from mdp_functions.errors import error_catalog

POLICY = {
    "prefix": "sandbox_",
    "handle_pattern": "^[a-z][a-z0-9_]{0,47}$",
    "object_pattern": "^[a-z][a-z0-9_]{0,62}$",
    "connection_limit": 4,
    "statement_timeout": "30s",
    "idle_in_transaction_session_timeout": "60s",
    "temp_file_limit": "256MB",
    "quota_bytes": 1073741824,
    "warn_fraction": 0.8,
    "poll_seconds": 60,
    "archive_root": "inputs/sandbox-archives",
    "runtime_roles": [
        "reader_wh",
        "showcase_wh",
        "api_key_reader",
        "dbt_transform",
        "loader_wh",
        "service_read",
        "functions_rt",
        "control_rt",
        "workbench_wh",
    ],
    "messages": {
        code: row["summary"] + " " + row["next_step"]
        for code, row in error_catalog().items()
        if code.startswith("sandbox_")
        or code
        in {
            "workbench_permission_denied",
            "workbench_statement_timeout",
            "workbench_query_failed",
        }
    },
}


def message(code):
    return POLICY["messages"][code]


def name(handle):
    import re

    if not re.fullmatch(POLICY["handle_pattern"], handle) or handle == "ro":
        raise ValueError(message("sandbox_handle_invalid"))
    return POLICY["prefix"] + handle

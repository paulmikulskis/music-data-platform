"""Shared fixtures for manifest validation tests."""


import json

from mdp_functions.runs import Runtime


def retained(rt: Runtime, load: dict) -> tuple[str, dict]:
    dump = rt.db.one("SELECT * FROM control.dump WHERE id=%s", (load["dump_id"],))
    key = next(f["key"] for f in dump["files"] if f["role"] == "manifest")
    return key, json.loads(rt.store.get(key))

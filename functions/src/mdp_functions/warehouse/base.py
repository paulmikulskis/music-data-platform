"""Warehouse protocol; landing owns the control claim, adapters own the row fence."""

from collections.abc import Callable
from typing import Any, Protocol

import duckdb
import psycopg
from mdp_functions.errors import ServiceError
from psycopg_pool import PoolTimeout

Hook = Callable[[str, Any], None]


class Warehouse(Protocol):
    def ensure(self, table: str, columns: dict[str, str]) -> None: ...
    def land(
        self,
        manifest: dict[str, Any],
        claim: dict[str, Any],
        parts: list[bytes],
        hook: Hook | None = None,
    ) -> int: ...
    def receipts(self, dump_ids: list[Any] | None = None) -> list[dict[str, Any]]: ...
    def mirror(self, tables: dict[str, list[dict[str, Any]]]) -> None: ...
    def stripped_cycles(self) -> list[str]: ...
    def health(self) -> None: ...


def mirror(warehouse: Warehouse, tables: dict[str, list[dict[str, Any]]]) -> None:
    # Callers read control snapshots before this boundary so their failures retain
    # control_db attribution, including while holding the publication lock.
    try:
        warehouse.mirror(tables)
    except (OSError, psycopg.Error, PoolTimeout, duckdb.Error) as exc:
        raise ServiceError(
            "warehouse_unavailable", "Warehouse mirror is unavailable", 503
        ) from exc



def stripped_cycles(warehouse: Warehouse) -> list[str]:
    try:
        return warehouse.stripped_cycles()
    except (OSError, psycopg.Error, PoolTimeout, duckdb.Error) as exc:
        raise ServiceError(
            "warehouse_unavailable", "Warehouse mirror is unavailable", 503
        ) from exc

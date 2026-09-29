"""Wire fields consumed by the control plane; counts remain decimal strings."""

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict


class RunRow(BaseModel):
    model_config = ConfigDict(extra="allow")
    id: UUID
    kind: Literal[
        "export", "close", "invoke", "repair", "backfill", "migrate", "workbench", "dbt"
    ]
    scope: str
    streamline_id: UUID | None
    cycle_id: UUID | None
    parent_run_id: UUID | None
    trace_id: str | None
    status: str
    coverage: Literal["full", "partial", "empty"] | None
    error_class: str | None
    error_message: str | None
    rows_written: str
    rows_rejected: str
    cost_cents: str
    created_at: datetime


class RegistryResponse(BaseModel):
    registered: int


class CostsyncResponse(BaseModel):
    reconciled: int


class ReferenceProbeResponse(BaseModel):
    """The Reference page's state for the MusicBrainz source and each reference alert class judged."""

    state: dict[str, object]
    alerts: dict[str, bool]


class OutputPreview(BaseModel):
    table: str
    rows: list[dict[str, object]]


class RowCount(BaseModel):
    at: str
    rows: str

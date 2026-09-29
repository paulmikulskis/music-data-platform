"""Validate retained manifests before landing or recovery uses any field."""

from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class ManifestModel(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid")


class Part(ManifestModel):
    key: str
    name: str | None = None
    role: Literal["output", "rejected", "payload", "media"]
    format: Literal["parquet", "jsonl.gz"]
    bytes: int = Field(ge=0)
    row_count: int = Field(ge=0)


class CursorExpected(ManifestModel):
    version: int = Field(ge=0)
    reset_generation: int = Field(ge=0)
    exists: bool


class DumpManifest(ManifestModel):
    id: UUID
    kind: Literal["output", "rejected"]
    run_id: UUID
    streamline_id: UUID
    cycle_id: UUID
    revision_id: UUID | None
    warehouse_id: UUID
    landed_seq: int = Field(ge=0)
    target_table: str
    prefix: str
    uri_prefix: str
    files: list[Part]
    row_count: int = Field(ge=0)
    bytes: int = Field(ge=0)
    columns: dict[str, str]
    schema_fingerprint: str
    function_version: str
    batch_id: UUID
    lease_token: UUID
    attempt_id: UUID
    page: int = Field(ge=1)
    page_id: UUID
    complete: bool
    completed_targets: list[str]
    cursor_changes: dict[str, Any]
    cursor_expected: dict[str, CursorExpected]
    observed: int = Field(ge=0)
    yielded: int = Field(ge=0)
    rejected: int = Field(ge=0)
    accounting_version: Literal[1, 2] = 1
    exclusions: dict[str, int] = Field(default_factory=dict)
    page_manifests: list[str] = Field(min_length=1)


def parse_manifest(data: bytes) -> dict[str, Any]:
    return DumpManifest.model_validate_json(data).model_dump(mode="json")

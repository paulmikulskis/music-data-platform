"""Named, transport-independent runtime errors."""

import json
from functools import lru_cache
from pathlib import Path


@lru_cache(maxsize=1)
def error_catalog() -> dict[str, dict]:
    return json.loads(Path(__file__).with_name("error_catalog.json").read_text())


def error_hint(error_class: str) -> dict:
    catalog = error_catalog()
    return catalog.get(error_class, catalog["unmapped"])


class ServiceError(Exception):
    def __init__(
        self,
        error_class: str,
        message: str,
        status_code: int = 409,
        *,
        vendor_status: int | None = None,
    ) -> None:
        super().__init__(message)
        self.error_class = error_class
        self.message = message
        self.status_code = status_code
        # The vendor's HTTP status when a response ended it; None for a transport failure.
        self.vendor_status = vendor_status

    @property
    def next_step(self) -> str:
        return error_hint(self.error_class)["next_step"]

    @property
    def runbook(self) -> str | None:
        slug = error_hint(self.error_class)["runbook"]
        return f"/runbooks/{slug}" if slug else None


class LeaseLost(ServiceError):
    def __init__(self) -> None:
        super().__init__("dump_late", "Batch lease or attempt no longer owns this page")

"""Collect pytest's actual Postgres markers, including fixture-added markers."""

import json
import os
from pathlib import Path


def pytest_collection_finish(session):
    Path(os.environ["MDP_READY_COLLECTION"]).write_text(
        json.dumps(
            {
                (
                    str(item.path.relative_to(Path.cwd()))
                    + "::"
                    + item.nodeid.split("::", 1)[1]
                ): bool(item.get_closest_marker("docker"))
                for item in session.items
            }
        )
    )

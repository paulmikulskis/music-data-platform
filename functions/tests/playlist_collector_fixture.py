"""Shared fixtures for playlist collectors tests."""


from pathlib import Path
from uuid import uuid4

import httpx
from mdp_functions.http import FixtureTransport
from mdp_functions.layers import Ctx, Target
from mdp_functions.playlist_targets import spec_hash
from mdp_functions.registry import discover

ROOT = Path(__file__).parents[1] / "src/mdp_functions/sources"


REPO = Path(__file__).parents[2]


SPEC = {
    "category_id": 0,
    "tag_norm_names": ["electronic"],
    "geoname_id": 0,
    "slice": "top",
    "time_facet_id": None,
    "include_result_types": ["a", "s"],
}


def target(platform, account, handle="x", **params):
    return Target(
        id=str(uuid4()),
        platform=platform,
        platform_account_id=account,
        handle=handle,
        params_json=params,
    )


TARGETS = {
    "sc_playlist": lambda: target("soundcloud", "921474645", owner_class="editorial"),
    "sc_curator_playlists": lambda: target("soundcloud", "9909210781"),
    "sc_hubs": lambda: None,
    "bc_discover": lambda: target(
        "bandcamp", "discover:" + spec_hash(SPEC), spec=SPEC, size=500
    ),
    "bc_daily_list": lambda: target(
        "bandcamp",
        "daily:best-electronic:2026-08",
        "best-electronic/synthetic-electronic-selection",
    ),
    "bc_radio": lambda: target("bandcamp", "radio:994"),
    "bc_fan_playlist": lambda: target(
        "bandcamp", "playlist:500001", "fan-example-a/playlist/post-hardcore"
    ),
    "bc_tralbum": lambda: target("bandcamp", "band:9144349973", "synthetic-label.bandcamp.com"),
}


SCENARIOS = [
    (key, path.stem)
    for key in TARGETS
    for path in sorted((ROOT / key / "fixtures").glob("*.jsonl"))
]


EXPECTED = {
    "partial": "partial",
    "normal": "full",
    "unauthorized": "full",
    "withheld": "header_only",
}


async def run(key, scenario="normal", batch=None, cursors=None, transport=None):
    manifest = discover()[key]
    ctx = Ctx(manifest, {"id": uuid4(), "cycle_id": uuid4(), "resolved_config": {"fixture": True}}, cursors)
    calls = []

    async def seen(request):
        calls.append(request)

    async with httpx.AsyncClient(
        transport=transport
        or FixtureTransport([ROOT / key / "fixtures" / f"{scenario}.jsonl"]),
        event_hooks={"request": [seen]},
    ) as client:
        ctx.http = client
        if batch is None and manifest.targets is None:
            await manifest.function(ctx)
        else:
            await manifest.function(ctx, batch or [TARGETS[key]()])
    return ctx, calls


class SeedConn:
    """Records seed statements; answers inserts with fresh ids."""

    def __init__(self):
        self.specs = []

    def execute(self, query, params=()):
        if "control.target_spec" in query:
            self.specs.append((params[0], params[2], params[3].obj))
        answer = [uuid4(), True]
        return type("Result", (), {"fetchone": lambda _self: answer})()

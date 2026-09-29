"""Tests for silver egress."""

import _socket
import socket
import subprocess

import pytest
from conftest import bound
from mdp_functions.layers import silver
from mdp_functions.registry import REGISTRY, sync
from test_enrichment_runtime import inputs


@pytest.mark.parametrize("kind", ["direct", "cached", "native", "dns", "curl"])
async def test_silver_all_egress_paths(rt, databases, kind):
    inputs(databases, 1)
    cached = socket.socket

    @silver(
        source_key="enrichment_egress_" + kind,
        reads=["marts.mart_enrichment_fixture"],
        writes=["raw.egress_" + kind],
    )
    async def probe(ctx, rows):
        if kind == "direct":
            socket.socket()
        elif kind == "cached":
            cached().connect(("127.0.0.1", 9))
        elif kind == "native":
            _socket.socket()
        elif kind == "dns":
            socket.getaddrinfo("example.invalid", 443)
        else:
            subprocess.run(["curl", "https://example.invalid"], check=False)  # noqa: ASYNC221 - deliberate forbidden native-client probe
        yield rows[0]

    try:
        sync(rt.db)
        _, run = await bound(rt, "enrichment_egress_" + kind)
        await rt.execute(run["id"])
        assert rt.db.one(
            "SELECT status,error_class FROM control.run WHERE id=%s", (run["id"],)
        ) == {"status": "failed", "error_class": "egress_blocked"}
    finally:
        REGISTRY.pop("enrichment_egress_" + kind)

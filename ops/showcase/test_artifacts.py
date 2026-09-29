"""Artifact boundaries and deterministic graph checks. Run with the functions environment."""

import copy
import datetime as dt
import importlib.util
import json
import os
import shutil
import sys
import tempfile
import unittest
from itertools import pairwise
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "ops/showcase/lineage"))
from generate import digest, generate, normalized_manifest, read
from permissions import projection_sql
from permissions import validate as permissions


def load(name, file):
    spec = importlib.util.spec_from_file_location(name, ROOT / file)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


stack = load("stack_collect", "ops/showcase/stack/collect.py")
artifacts = load("artifacts", "ops/showcase/artifacts.py")


class Artifacts(unittest.TestCase):
    def test_manifest_ignores_local_metadata(self):
        manifest = read(ROOT / "dbt/target/manifest.json")
        changed = copy.deepcopy(manifest)
        changed["metadata"] = {"generated_at": "tomorrow", "invocation_id": "new"}
        for node in changed["nodes"].values():
            node["compiled_path"] = "/private-sentinel/elsewhere"
            node["created_at"] = 999
        self.assertEqual(
            digest(normalized_manifest(manifest)), digest(normalized_manifest(changed))
        )
        self.assertEqual(generate(manifest=manifest), generate(manifest=changed))

    def test_graph_has_real_edges_and_no_runtime_claims(self):
        graph = generate()
        ids = {n["id"] for n in graph["nodes"]}
        self.assertTrue(
            all(e["from"] in ids and e["to"] in ids for e in graph["edges"])
        )
        self.assertTrue(
            all("enabled" not in n and "row_count" not in n for n in graph["nodes"])
        )
        self.assertTrue(all("tenant" not in r for r in graph["peeks"]))
        edges = {(e["from"], e["to"]) for e in graph["edges"]}
        for edge in graph["simple_edges"]:
            path = [edge["from"], *edge["via"], edge["to"]]
            self.assertTrue(all(pair in edges for pair in pairwise(path)))

    def test_retention_blocks_downstream_projection(self):
        import generate as generator

        declarations = generator.declarations(ROOT)
        declarations["sz_chart"]["retain_days"] = 30
        with (
            patch.object(generator, "declarations", return_value=declarations),
            self.assertRaisesRegex(ValueError, "Unsafe peek"),
        ):
            generate()

    def test_private_fly_fields_never_serialize(self):
        sentinels = [
            "client-database-sentinel",
            "private.internal",
            "10.20.30.40",
            "machine-sentinel",
            "volume-sentinel",
            "registry/private:image",
        ]

        def fetch(app, kind):
            if kind == "volumes":
                return [
                    {
                        "id": sentinels[4],
                        "name": sentinels[0],
                        "size_gb": 10,
                        "encrypted": True,
                    }
                ]
            return [
                {
                    "id": sentinels[3],
                    "name": sentinels[0],
                    "private_ip": sentinels[2],
                    "hostname": sentinels[1],
                    "region": "ewr",
                    "config": {
                        "metadata": {
                            "fly_process_group": "app",
                            "deploy": sentinels[5],
                        },
                        "guest": {"cpus": 1, "memory_mb": 512},
                        "mounts": [{"volume": sentinels[4]}],
                        "image": sentinels[5],
                    },
                }
            ]

        value = stack.collect(fetch=fetch)
        stack.validate(value)
        serialized = json.dumps(value)
        for sentinel in sentinels:
            self.assertNotIn(sentinel, serialized)
        self.assertTrue(
            all(s["status"]["state"] == "not_checked" for s in value["services"])
        )
        changed = copy.deepcopy(value)
        changed["services"][0]["alias"] = sentinels[1]
        changed["input_hashes"]["measurements"] = digest(changed["services"])
        with self.assertRaises(AssertionError):
            stack.validate(changed)

    def test_stale_or_mismatched_artifacts_fail(self):
        graph = read(ROOT / "control/apps/showcase/lib/lineage.generated.json")
        now = dt.datetime.now(dt.timezone.utc)
        value = stack.collect(now=now.isoformat())
        artifacts.validate(ROOT, graph, value, now)
        for changed in [
            {**value, "captured_at": (now - dt.timedelta(days=8)).isoformat()},
            {**value, "captured_at": (now + dt.timedelta(hours=1)).isoformat()},
        ]:
            with self.assertRaises(ValueError):
                artifacts.validate(ROOT, graph, changed, now)
        with self.assertRaises(ValueError):
            artifacts.validate(ROOT, {**graph, "input_hashes": {}}, value, now)
        with tempfile.TemporaryDirectory() as directory:
            payload = Path(directory)
            (payload / "lineage.generated.json").write_text(json.dumps(graph))
            (payload / "stack.generated.json").write_text(json.dumps(value))
            context = payload / "context"
            for folder in (
                "ops/showcase/stack",
                "ops/showcase/links",
                "control/apps/showcase/lib",
            ):
                shutil.copytree(ROOT / folder, context / folder)
            manifest = read(context / "ops/showcase/links/links.json")
            manifest["links"] = []
            (context / "ops/showcase/links/links.json").write_text(json.dumps(manifest))
            links = {
                "schema_version": 1,
                "revision": "a" * 40,
                "collected_at": now.isoformat(),
                "input_hashes": artifacts.links_module.inputs(context),
                "names": "names_not_checked",
                "tenants": "not_checked",
                "entries": [],
            }
            (payload / "links.generated.json").write_text(json.dumps(links))
            envelope = {
                "schema_version": 1,
                "revision": "a" * 40,
                "lineage_hash": digest(graph),
                "stack_hash": digest(value),
                "links_hash": digest(links),
                "lineage_inputs": graph["input_hashes"],
                "stack_inputs": value["input_hashes"],
                "links_inputs": links["input_hashes"],
            }
            (payload / "artifacts.build.json").write_text(json.dumps(envelope))
            artifacts.check_image(context, payload, "a" * 40)
            with self.assertRaisesRegex(ValueError, "Image envelope differs"):
                artifacts.check_image(context, payload, "b" * 40)
            changed_links = {**links, "collected_at": "2000-01-01T00:00:00Z"}
            (payload / "links.generated.json").write_text(json.dumps(changed_links))
            with self.assertRaisesRegex(ValueError, "Image envelope differs"):
                artifacts.check_image(context, payload, "a" * 40)
            (payload / "links.generated.json").write_text(json.dumps(links))
            value["services"].pop()
            (payload / "stack.generated.json").write_text(json.dumps(value))
            with self.assertRaises((ValueError, AssertionError)):
                artifacts.check_image(context, payload, "a" * 40)

    def test_link_sentinels_stop_overlay_installation(self):
        from test_links import SENTINELS, Links

        fixture = Links()
        fixture.setUp()
        try:
            context = fixture.root
            shutil.copytree(ROOT / "ops/showcase/stack", context / "ops/showcase/stack")
            overlay = context / "overlay"
            overlay.mkdir()
            (overlay / "stack.generated.json").write_text(json.dumps(stack.collect()))
            baseline = fixture.collect()
            os.environ["MDP_SHOWCASE_DENY_NAMES"] = "\n".join(SENTINELS[:2])
            for sentinel in SENTINELS:
                for field in ("body", "highlights", "headings"):
                    changed = copy.deepcopy(baseline)
                    changed["entries"][0][field].append(sentinel)
                    (overlay / "links.generated.json").write_text(json.dumps(changed))
                    with (
                        self.subTest(field=field, sentinel=SENTINELS.index(sentinel)),
                        self.assertRaises(ValueError),
                    ):
                        artifacts.install(context, overlay, "a" * 40)
                    self.assertFalse(
                        (context / artifacts.LIB / "artifacts.build.json").exists()
                    )
        finally:
            fixture.doCleanups()



if __name__ == "__main__":
    unittest.main()

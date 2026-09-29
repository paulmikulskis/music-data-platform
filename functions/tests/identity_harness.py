"""A disposable Postgres warehouse for the identity chain's cycle-bound SQL.

It creates every raw table the functions declare, lands rows as stamped dumps, mirrors closed cycles
with their close numbers, and runs dbt on the non-local `pg_local` target bound to a crafted cycle
through DBT_CLOUD_RUN_ID, so every manifest filter reads real stamps. Invokes, exports and closes are
excluded: gold outputs land here as the runtime would land them.
"""

import hashlib
import json
import os
import subprocess
from datetime import UTC, datetime, timedelta
from itertools import count
from pathlib import Path
from typing import Any
from uuid import NAMESPACE_URL, uuid4, uuid5

import psycopg
from mdp_functions.exporter import ensure_raw
from mdp_functions.playlist import PlaylistItem, PlaylistSnapshot
from psycopg import sql
from psycopg.conninfo import conninfo_to_dict, make_conninfo
from psycopg.types.json import Jsonb

REPO = Path(__file__).resolve().parents[2]
SEQ = count(1000)


def ident(value: str) -> str:
    return str(uuid5(NAMESPACE_URL, "mdp-identity:" + value))


class Warehouse:
    def __init__(self, admin_url: str, schema_root: Path) -> None:
        self.name = "mbt_wh_" + uuid4().hex[:10]
        self.admin_url = admin_url
        with psycopg.connect(admin_url, autocommit=True) as conn:
            conn.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(self.name)))
        options = conninfo_to_dict(admin_url)
        options["dbname"] = self.name
        self.url = make_conninfo(**options)
        self.options = options
        ensure_raw(self.url, schema_root)
        # Match the disposable server connection settings, including local TLS.
        self.profiles = schema_root / "profiles"
        self.profiles.mkdir(exist_ok=True)
        (self.profiles / "profiles.yml").write_text(
            "music_data_platform:\n  target: pg_local\n  outputs:\n    pg_local:\n      type: postgres\n"
            f"      host: \"{options.get('host', '127.0.0.1')}\"\n      port: {options.get('port', 5432)}\n"
            f"      user: \"{options['user']}\"\n      pass: \"{options['password']}\"\n      dbname: \"{self.name}\"\n"
            f"      schema: dbt\n      threads: 4\n      sslmode: {options.get('sslmode', 'prefer')}\n      autocommit: false\n")
        with self.connect() as conn:
            conn.execute("CREATE SCHEMA IF NOT EXISTS mdp")
            # The bind hook returns SQL the executor runs; binding here is the crafted raw.cycles rows.
            conn.execute("CREATE OR REPLACE FUNCTION mdp.bind_cycle(VARIADIC text[]) RETURNS text LANGUAGE sql AS $$ SELECT 'bound' $$")
            conn.execute("CREATE TABLE IF NOT EXISTS mdp.pseudonym_key (key text)")
            conn.execute("INSERT INTO mdp.pseudonym_key VALUES ('harness')")

    def drop(self) -> None:
        with psycopg.connect(self.admin_url, autocommit=True) as conn:
            conn.execute(sql.SQL("DROP DATABASE {} WITH (FORCE)").format(sql.Identifier(self.name)))

    def connect(self) -> psycopg.Connection:
        return psycopg.connect(self.url, autocommit=True)

    def query(self, statement: str, params: Any = None) -> list[tuple]:
        with self.connect() as conn:
            return conn.execute(statement, params).fetchall()

    # Cycles and stamps, as the close and its mirror catch-up write them.
    def cycle(self, cadence: str, close_no: int, closed_at: datetime, scope: str = "global",
              tenant_close_nos: dict[str, int] | None = None) -> dict[str, Any]:
        cycle_id = str(uuid4())
        run_id = f"harness:{cadence}:{close_no}:{cycle_id[:8]}"
        with self.connect() as conn:
            conn.execute(
                "INSERT INTO raw.cycles(id,cadence,scope,opened_at,opened_by_dbt_run_id,closed_at,status,manifest_mode,"
                "close_no,global_close_no,global_inputs,tenant_close_nos) VALUES (%s,%s,%s,%s,%s,%s,'closed','stamp',%s,NULL,'[]',%s)",
                (cycle_id, cadence, scope, closed_at - timedelta(minutes=5), run_id, closed_at, close_no,
                 Jsonb(tenant_close_nos or {})),
            )
            conn.execute(
                "INSERT INTO raw.cycle_attempts(dbt_run_id,cycle_id,bound_at,reason_category) VALUES (%s,%s,%s,'scheduled')",
                (run_id, cycle_id, closed_at - timedelta(minutes=5)),
            )
        return {"id": cycle_id, "run_id": run_id, "cadence": cadence, "close_no": close_no, "closed_at": closed_at}

    def land(self, table: str, rows: list[dict[str, Any]], close_no: int | None, source_key: str,
             cycle_id: str | None = None, run_id: str | None = None, scope: str = "global",
             target_id: str | None = None) -> str:
        """One dump of rows; stamped at close_no, or left unstamped (a load not yet committed)."""
        dump_id, seq = str(uuid4()), next(SEQ)
        lineage = {"_run_id": run_id or str(uuid4()), "_dump_id": dump_id, "_landed_seq": seq,
                   "_cycle_id": cycle_id or str(uuid4()), "_revision_id": None, "_target_id": target_id,
                   "_request_id": "harness", "_source_key": source_key, "_ingested_at": datetime.now(UTC), "_extra": {}}
        with self.connect() as conn:
            for row in rows:
                record = {**row, **lineage}
                conn.execute(
                    sql.SQL("INSERT INTO {} ({}) VALUES ({})").format(
                        sql.Identifier(*table.split(".")), sql.SQL(",").join(map(sql.Identifier, record)),
                        sql.SQL(",").join(sql.Placeholder() * len(record))),
                    [Jsonb(v) if isinstance(v, (dict, list)) else v for v in record.values()],
                )
        if close_no is not None:
            self.stamp(dump_id, table, close_no, source_key, scope)
        return dump_id

    def stamp(self, dump_id: str, table: str, close_no: int, source_key: str = "harness", scope: str = "global") -> None:
        with self.connect() as conn:
            conn.execute("INSERT INTO raw.dump_stamps(dump_id,scope,close_no,source_key,target_table) VALUES (%s,%s,%s,%s,%s)",
                         (dump_id, scope, close_no, source_key, table))

    # MusicBrainz generations, as mb_spine and the runtime land them.
    def generation(self, generation: str, close_no: int, tables: dict[str, list[dict[str, Any]]],
                   unstamped: tuple[str, ...] = ()) -> dict[str, str]:
        run_id, cycle_id = str(uuid4()), str(uuid4())
        stamp = {"mb_channel": "dump", "mb_generation": generation, "mb_sequence": 189207}
        outputs, dumps, counts = {}, {}, {}
        for name, rows in tables.items():
            table = "raw.mb_" + name
            dump = self.land(table, [{**stamp, **r} for r in rows], None if name in unstamped else close_no,
                             "mb_spine", cycle_id, run_id)
            outputs[table] = {dump: len(rows)}
            dumps[name] = dump
            counts[table] = len(rows)
        gen = self.land("raw.mb_generation", [{**stamp, "mb_key": generation, "export_date": datetime(2026, 9, 23, tzinfo=UTC),
                                               "schema_sequence": 31, "imported_at": datetime(2026, 9, 23, tzinfo=UTC),
                                               "validated_at": datetime(2026, 9, 23, tzinfo=UTC),
                                               "mirror_counts": json.dumps(counts), "read_at": datetime.now(UTC)}],
                        close_no, "mb_spine", cycle_id, run_id)
        outputs["raw.mb_generation"] = {gen: 1}
        self.land("raw._run_completion", [{"run_id": run_id, "source_key": "mb_spine", "outputs": outputs,
                                           "recorded": {"generation": generation, "sequence": 189207, "mirror_counts": counts}}],
                  close_no, "mb_spine", cycle_id, run_id)
        return dumps

    # Playlist observations, as sp_playlist and am_playlist land them.
    def observation(self, platform: str, playlist_id: str, observed_at: datetime, tracks: list[dict[str, Any]],
                    owner_class: str, close_no: int | None, snapshot_close_no: int | None = "same", surface: str | None = None,
                    group: str | None = None, variant: str = "US", followers: int | None = None,
                    target_id: str | None = None, cadence: str = "daily") -> str:
        surface = surface or ("am_playlist" if platform == "apple_music" else "sp_playlist_embed")
        stream = "full" if platform == "apple_music" else "head"
        sid = ident(f"{platform}:{playlist_id}:{variant}:{observed_at.isoformat()}:{surface}")
        common = {"platform": platform, "playlist_id": playlist_id, "variant": variant, "stream": stream,
                  "observation_group": group, "snapshot_id": sid, "observed_at": observed_at, "fetch_surface": surface}
        items, seen = [], {}
        for position, t in enumerate(tracks, 1):
            seen[t["id"]] = nth = seen.get(t["id"], 0) + 1
            items.append(PlaylistItem(
                **common, position=position, item_type="track", platform_item_id=t["id"], platform_track_id=t["id"],
                occurrence=nth, occurrence_key=f"track:{t['id']}#{nth}", occurrence_inferred=True, title=t["title"],
                artist_names=t.get("artists", []), platform_artist_ids=t.get("artist_ids", []),
                platform_album_id=t.get("album"), duration_ms=t.get("duration"), isrc=t.get("isrc"),
            ).model_dump(mode="python"))
        digest = hashlib.md5(json.dumps([[i["position"], i["occurrence_key"]] for i in items]).encode()).hexdigest()
        snapshot = PlaylistSnapshot(
            **common, coverage="full", items_observed=len(items), title=playlist_id, owner_class=owner_class,
            track_count_reported=len(items), cadence=cadence, observation="content", snapshot_hash=digest,
            membership_hash=digest, content_hash=digest, followers=followers,
        ).model_dump(mode="python")
        source = "am_playlist" if platform == "apple_music" else "sp_playlist"
        self.land("raw.playlist_items", items, close_no, source, target_id=target_id)
        self.land("raw.playlist_snapshots", [snapshot], close_no if snapshot_close_no == "same" else snapshot_close_no, source,
                  target_id=target_id)
        return sid

    def dbt(self, cycle: dict[str, Any], select: str, extra: tuple[str, ...] = (), vars_: dict | None = None,
            command: str = "build") -> str:
        env = {**os.environ, "MDP_PG_HOST": self.options.get("host", "127.0.0.1"), "MDP_PG_PORT": str(self.options.get("port", 5432)),
               "MDP_PG_USER": self.options["user"], "MDP_PG_PASSWORD": self.options["password"], "MDP_PG_DB": self.name,
               "MDP_PG_SCHEMA": "dbt", "DBT_CLOUD_RUN_ID": cycle["run_id"], "DBT_MDP_CADENCE": cycle["cadence"],
               "DBT_MDP_SCOPE": "global", "DBT_CLOUD_JOB_ID": "harness", "TZ": "America/New_York"}
        args = ["uv", "run", "--project", "dbt", "dbt", command, "--project-dir", "dbt", "--profiles-dir", str(self.profiles),
                "--target", "pg_local", "--target-path", str(self.profiles / "target"), "--log-path", str(self.profiles / "logs"),
                "--indirect-selection", "cautious", "--select", *select.split(), "--exclude", "tag:invoke", "tag:export", "tag:close",
                *extra]
        if vars_:
            args += ["--vars", json.dumps(vars_)]
        result = subprocess.run(args, cwd=REPO, env=env, capture_output=True, text=True, check=False)
        if result.returncode:
            raise AssertionError(result.stdout[-6000:] + result.stderr[-2000:])
        return result.stdout

    def revision(self, target_set: str, members: list[tuple[str, str, str]], taken_at: datetime, close_no: int) -> None:
        """A frozen playlist target revision: its members (target id, platform, playlist id), counted from
        the closed cycle that exported it."""
        cycle = self.cycle("daily", close_no, taken_at + timedelta(minutes=1))
        revision = str(uuid4())
        with self.connect() as conn:
            for target_id, platform, playlist_id in members:
                conn.execute(
                    "INSERT INTO raw.targets(id,platform,platform_account_id,handle,role,target_set_id,resource_kind,canonical_key,"
                    "params_json,taken_at,_cycle_id,_revision_id) VALUES (%s,%s,%s,%s,NULL,%s,'playlist',%s,'{}',%s,%s,%s)",
                    (target_id, platform, playlist_id, playlist_id, target_set, f"{platform}:{playlist_id}", taken_at, cycle["id"], revision))

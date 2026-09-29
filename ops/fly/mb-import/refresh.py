#!/usr/bin/env python3
"""Monthly refresh of the private MusicBrainz mirror.

Each generation lives in its own database; readers always use musicbrainz_db. A refresh imports the
newest CC0 full export into musicbrainz_next on a one-off mdp-mb-import machine with a temporary
volume, waits for import.sh to record it validated, promotes it with promote.sql (the old
musicbrainz_db is renamed musicbrainz_prev and dropped after the swap commits), then destroys the
machine and volume. Every step lands in mdp_meta.import_run. Fly calls go through the guarded
ops/fly/mb-db/fly.py wrapper; SQL runs on mdp-mb-db over its SSH path.

Run it with Fly credentials in ~/.fly/config.yml, from any directory:

    python3 ops/fly/mb-import/refresh.py --dry-run
    python3 ops/fly/mb-import/refresh.py --status
    secret_store run -- \\
        uv run --project functions python ops/fly/mb-import/refresh.py --if-requested

Exit codes: 0 done or nothing to do; 1 refused or reference_import_failed; 2 promoted but the
temporary machine or volume is left; 3 reference_disk_high.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import tomllib

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
sys.path.insert(0, str(ROOT / 'ops/fly/mb-db'))
import fly
import mirror
from mirror import MirrorError, Names, bundle, prelude, records

LATEST_URL = 'https://data.metabrainz.org/pub/musicbrainz/data/fullexport/LATEST'
ORG = mirror.ORG
IMPORT_APP = 'mdp-mb-import'
REGION = 'ewr'
TEMP_VOLUME = 'mbrefresh'
MACHINE_PREFIX = 'mdp-refresh-'
BUILD_CONTEXT = ROOT / 'ops/fly'
DISK_LIMIT = 0.80
GROWTH = 1.10  # a new generation is sized as the serving one plus 10%
STALE_DAYS = 8  # upstream publishes full exports twice a week
LIVE_STATES = {'created', 'starting', 'started', 'replacing', 'updating'}
EXIT_FAILED, EXIT_CLEANUP, EXIT_DISK = 1, 2, 3


class Failure(RuntimeError):
    pass


def sql_literal(text: str) -> str:
    return "'" + text.replace('\x00', '').replace("'", "''") + "'"


def pct(ratio: float) -> str:
    return f'{ratio * 100:.1f}%'


def disk_verdict(used: int, serving: int, total: int, refresh: bool = True) -> str | None:
    """reference_disk_high when databases use >= 80% of the volume now, or, before a refresh, when a
    second generation (the serving one plus 10%) would take them there."""
    if not total:
        return 'reference_disk_high: volume.total_bytes is unknown; refusing to start'
    if used / total >= DISK_LIMIT:
        return (f'reference_disk_high: databases use {pct(used / total)} of the mirror volume '
                f'({used} of {total} bytes); imports pause until the volume is expanded')
    projected = (used + GROWTH * serving) / total
    if refresh and projected >= DISK_LIMIT:
        return (f'reference_disk_high: a second generation would take the mirror volume to {pct(projected)} '
                f'({used} used + {int(GROWTH * serving)} expected of {total} bytes); expand the volume first')
    return None


def upstream_latest() -> str:
    with urllib.request.urlopen(LATEST_URL, timeout=30) as response:
        stamp = response.read().decode().strip()
    if not mirror.STAMP.match(stamp):
        raise Failure(f'upstream LATEST is not an export stamp: {stamp[:40]!r}')
    return stamp


def importer_config() -> tuple[dict[str, str], dict[str, Any]]:
    """[env] and [[vm]] from fly.toml: the single declaration of the importer machine."""
    conf = tomllib.loads((HERE / 'fly.toml').read_text())
    vm = conf['vm'][0]
    size = re.fullmatch(r'(\d+)\s*(gb|mb)', vm['memory'].lower())
    if not size:
        raise ValueError(vm['memory'])
    memory_mb = int(size.group(1)) * (1024 if size.group(2) == 'gb' else 1)
    return {k: str(v) for k, v in conf['env'].items()}, {'size': vm['size'], 'memory_mb': memory_mb}


def machine_env(names: Names, run_id: int | str, initial: bool) -> dict[str, str]:
    env, _ = importer_config()
    return env | {'MDP_IMPORT_MODE': 'initial' if initial else 'refresh', 'MDP_IMPORT_RUN_ID': str(run_id),
                  'MDP_SERVING_DB': names.serving, 'MDP_NEXT_DB': names.next, 'MDP_META_DB': names.meta}


def machine_run_args(name: str, volume_id: str, env: dict[str, str], vm: dict[str, Any], run_id: int | str) -> list[str]:
    args = ['machine', 'run', '.', '--dockerfile', 'mb-import/Dockerfile', '--app', IMPORT_APP,
            '--region', REGION, '--name', name, '--vm-size', vm['size'], '--vm-memory', str(vm['memory_mb']),
            '--volume', f'{volume_id}:/media/dbdump', '--restart', 'no', '--detach',
            '--metadata', f'mdp_run_id={run_id}']
    for key, value in env.items():
        args += ['--env', f'{key}={value}']
    return args + ['--org', ORG]


def volume_create_args(size_gb: int) -> list[str]:
    return ['volumes', 'create', TEMP_VOLUME, '--app', IMPORT_APP, '--region', REGION, '--size', str(size_gb),
            '--scheduled-snapshots=false', '--yes', '--json', '--org', ORG]


def key(obj: dict[str, Any], name: str) -> Any:
    """flyctl JSON uses lowercase keys; tolerate the capitalised Go field names too."""
    return obj.get(name, obj.get(name.capitalize(), obj.get(name.upper())))


class FlyOps:
    """The importer's Fly resources, through fly.py (org and app ownership are checked there)."""

    def _call(self, args: list[str], **kwargs: Any):
        return fly.run(args, **kwargs)

    def _checked(self, args: list[str], **kwargs: Any) -> str:
        result = self._call(args, capture_output=True, text=True, **kwargs)
        if result.returncode:
            tail = ' | '.join((result.stderr or result.stdout or '').strip().splitlines()[-3:])
            raise Failure(f"fly {' '.join(args[:2])} failed (exit {result.returncode}): {tail}")
        return result.stdout

    def create_volume(self, size_gb: int) -> str:
        return key(json.loads(self._checked(volume_create_args(size_gb))), 'id')

    def run_machine(self, name: str, volume_id: str, env: dict[str, str], vm: dict[str, Any], run_id: int) -> None:
        # The remote build streams to the operator's terminal; nothing in it is secret.
        result = self._call(machine_run_args(name, volume_id, env, vm, run_id), cwd=BUILD_CONTEXT)
        if result.returncode:
            raise Failure(f'fly machine run {name} failed (exit {result.returncode})')

    def _list(self, args: list[str]) -> list[dict[str, Any]]:
        out = self._checked(args).strip()
        return json.loads(out) if out.startswith('[') else []  # an empty app may print a sentence

    def machines(self) -> list[dict[str, Any]]:
        return self._list(['machine', 'list', '--app', IMPORT_APP, '--json', '--org', ORG])

    def machine(self, name: str) -> dict[str, Any] | None:
        return next((m for m in self.machines() if key(m, 'name') == name), None)

    def volumes(self) -> list[dict[str, Any]]:
        return self._list(['volumes', 'list', '--app', IMPORT_APP, '--json', '--org', ORG])

    def stop_machine(self, machine_id: str) -> None:
        self._checked(['machine', 'stop', machine_id, '--app', IMPORT_APP, '--org', ORG])

    def destroy_machine(self, machine_id: str) -> None:
        self._checked(['machine', 'destroy', machine_id, '--app', IMPORT_APP, '--force', '--org', ORG])

    def destroy_volume(self, volume_id: str) -> None:
        self._checked(['volumes', 'destroy', volume_id, '--app', IMPORT_APP, '--yes', '--org', ORG])


def mounted_volumes(machine: dict[str, Any] | None) -> list[str]:
    config = (machine and key(machine, 'config')) or {}
    return [key(m, 'volume') for m in (key(config, 'mounts') or []) if key(m, 'volume')]


class ControlRequests:
    """control.reference_source.reimport_requested_at for musicbrainz, as control_rt."""

    def __init__(self, url: str | None) -> None:
        if not url:
            raise Failure('MDP_CONTROL_RT_URL is not set; --if-requested needs it')
        self.url = url

    def _connect(self):
        import psycopg  # only --if-requested needs the control plane
        return psycopg.connect(self.url, autocommit=True)

    def pending(self) -> datetime | None:
        with self._connect() as conn:
            row = conn.execute("SELECT reimport_requested_at FROM control.reference_source "
                               "WHERE source = 'musicbrainz'").fetchone()
        return row[0] if row else None

    def clear(self, seen: datetime) -> None:
        """Clear the request this refresh served; a newer request stays pending."""
        with self._connect() as conn:
            conn.execute("UPDATE control.reference_source SET reimport_requested_at = NULL, "
                         "reimport_requested_by = NULL, updated_at = now() "
                         "WHERE source = 'musicbrainz' AND reimport_requested_at <= %s", (seen,))


@dataclass
class Deps:
    sql: Any = field(default_factory=mirror.FlySsh)
    fly: Any = field(default_factory=FlyOps)
    latest: Callable[[], str] = upstream_latest
    control: Callable[[], Any] = lambda: ControlRequests(os.environ.get('MDP_CONTROL_RT_URL'))
    names: Names = field(default_factory=Names)
    sleep: Callable[[float], None] = time.sleep
    clock: Callable[[], float] = time.monotonic
    out: Callable[[str], None] = print


# import_run ------------------------------------------------------------------------------------

def start_run(sql: Any, names: Names, generation: str, target: str) -> int:
    row = records(sql.psql(names.meta, prelude(generation=generation, target_db=target) + """
INSERT INTO public.import_run (generation, target_db, state, phase)
VALUES (:'generation', :'target_db', 'running', 'provision')
RETURNING 'MDP_JSON ' || json_build_object('run_id', id);
"""))
    return int(row['run_id'])


def run_row(sql: Any, names: Names, run_id: int) -> dict[str, Any]:
    row = records(sql.psql(names.meta, prelude(run_id=run_id) + """
SELECT 'MDP_JSON ' || json_build_object('run', row_to_json(r)) FROM public.import_run r WHERE id = :run_id;
"""))
    if not row.get('run'):
        raise Failure(f'import_run {run_id} is missing')
    return row['run']


def note_run(sql: Any, names: Names, run_id: int, phase: str, message: str) -> None:
    sql.psql(names.meta, prelude(run_id=run_id, phase=phase) + f"""
UPDATE public.import_run SET phase = :'phase', message = {sql_literal(message)} WHERE id = :run_id;
""")


def fail_run(sql: Any, names: Names, run_id: int, message: str) -> None:
    """state='failed' with the class in message; a run that already failed or promoted is left alone."""
    if not message.startswith('reference_'):
        message = f'reference_import_failed: {message}'
    sql.psql(names.meta, prelude(run_id=run_id) + f"""
UPDATE public.import_run SET state = 'failed', finished_at = now(), message = {sql_literal(message[:2000])}
WHERE id = :run_id AND state IN ('running', 'validated');
""")


# mirror databases ------------------------------------------------------------------------------

def promote(sql: Any, names: Names, run_id: int, generation: str) -> None:
    script = prelude(run_id=run_id, generation=generation, serving_db=names.serving, next_db=names.next,
                     prev_db=names.prev, meta_db=names.meta) + bundle(HERE / 'promote.sql')
    try:
        result = records(sql.psql(names.meta, script))
    except MirrorError:
        reopen_serving(sql, names)
        raise
    if result.get('promoted') != generation:
        raise Failure('promote.sql did not report the promoted generation')


def reopen_serving(sql: Any, names: Names) -> None:
    """Best effort: an interrupted promotion must not leave the serving database refusing readers."""
    try:
        sql.psql('postgres', prelude(serving_db=names.serving) + r"""
SELECT format('ALTER DATABASE %I WITH ALLOW_CONNECTIONS true', :'serving_db')
WHERE EXISTS (SELECT FROM pg_database WHERE datname = :'serving_db' AND NOT datallowconn) \gexec
""")
    except MirrorError:
        pass


def drop_attempt_databases(sql: Any, names: Names) -> None:
    sql.psql('postgres', prelude(next_db=names.next, prev_db=names.prev) + r"""
SELECT format('DROP DATABASE %I WITH (FORCE)', d) FROM unnest(ARRAY[:'next_db', :'prev_db']) AS d
WHERE EXISTS (SELECT FROM pg_database WHERE datname = d) \gexec
""")


def preflight(state: dict[str, Any], names: Names, initial: bool, resume: bool) -> list[str]:
    dbs, run, problems = set(state['databases']), state['run'], []
    if names.serving in state.get('closed', []):
        problems.append(f'{names.serving} refuses connections after an interrupted promotion; '
                        'run --discard-prior to reopen it')
    if initial and names.serving in dbs:
        problems.append(f'{names.serving} exists; --initial loads only an empty server')
    if not initial and not (state['serving'] or {}).get('validated_at'):
        problems.append(f'{names.serving} has no validated mdp.generation; run ops/fly/mb-db/apply-mdp-schema.py first')
    resumable = bool(resume and run and run['state'] == 'validated' and run['target_db'] == names.next)
    if resume and not resumable:
        problems.append('--resume needs the newest import_run to be validated into ' + names.next)
    if run and run['state'] == 'running':
        problems.append(f"run {run['id']} is still running (phase {run['phase']}); wait for it, "
                        'or run --discard-prior if its importer is gone')
    if run and run['state'] == 'validated' and not resume:
        problems.append(f"run {run['id']} validated {run['generation']} and waits for promotion; "
                        'rerun with --resume, or --discard-prior to drop it')
    if names.next in dbs and not resumable:
        problems.append(f'{names.next} exists from an earlier attempt; inspect it, then run --discard-prior')
    if names.prev in dbs:
        problems.append(f'{names.prev} is left from an earlier promotion; run --discard-prior to drop it')
    return problems


# the refresh ------------------------------------------------------------------------------------

def wait_for(deps: Deps, run_id: int, name: str, want: str, timeout_s: float, poll_s: float) -> dict[str, Any]:
    """Bounded polling. The machine is read before the run row, so a machine that has stopped while
    its run still says running really exited without recording an outcome."""
    deadline, misses, machine = deps.clock() + timeout_s, 0, None
    while True:
        try:
            machine = deps.fly.machine(name)
            run = run_row(deps.sql, deps.names, run_id)
        except (Failure, MirrorError) as error:
            misses += 1
            deps.out(f'poll failed ({misses}/3): {error}')
            if misses >= 3:
                raise Failure(f'lost contact while waiting for run {run_id}: {error}') from error
        else:
            misses = 0
            if run['state'] == want:
                return run
            if run['state'] != 'running':
                raise Failure(run['message'] or f"run {run_id} ended {run['state']}")
            state = key(machine, 'state') if machine else 'missing'
            if state not in LIVE_STATES:
                raise Failure(f"importer {name} is {state} in phase {run['phase']} without a recorded outcome")
            deps.out(f"run {run_id}: phase {run['phase']}, machine {state}")
        if deps.clock() >= deadline:
            if machine:
                try:
                    deps.fly.stop_machine(key(machine, 'id'))
                except Failure:
                    pass
            raise Failure(f'importer exceeded {timeout_s / 3600:.0f} h; machine {name} stopped for inspection')
        deps.sleep(poll_s)


def destroy_volume(deps: Deps, volume_id: str) -> None:
    for attempt in range(6):  # a volume detaches a few seconds after its machine is destroyed
        try:
            deps.fly.destroy_volume(volume_id)
            return
        except Failure:
            if attempt == 5:
                raise
            deps.sleep(10)


def cleanup(deps: Deps, run_id: int, name: str, volume_ids: list[str]) -> bool:
    try:
        machine = deps.fly.machine(name)
        volume_ids = sorted(set(volume_ids) | set(mounted_volumes(machine)))
        if machine:
            deps.fly.destroy_machine(key(machine, 'id'))
        for volume_id in volume_ids:
            destroy_volume(deps, volume_id)
        note_run(deps.sql, deps.names, run_id, 'done', f'promoted; destroyed {name} and {", ".join(volume_ids) or "no volume"}')
        return True
    except (Failure, MirrorError) as error:
        deps.out(f'Cleanup incomplete: {error}. Destroy by hand with ops/fly/mb-db/fly.py: '
                 f'machine {name} and volumes {volume_ids} on {IMPORT_APP}.')
        return False


def finish(deps: Deps, run: dict[str, Any], request: Any, control: Any, volume_ids: list[str]) -> int:
    run_id, name = int(run['id']), f"{MACHINE_PREFIX}{run['id']}"
    if request is not None:
        control.clear(request)
        deps.out(f'Cleared the re-import request from {request}.')
    return 0 if cleanup(deps, run_id, name, volume_ids) else EXIT_CLEANUP


def refresh(args: argparse.Namespace, deps: Deps) -> int:
    names, sql, out = deps.names, deps.sql, deps.out
    initial = args.initial
    state = mirror.status(sql, names)
    problems = preflight(state, names, initial, args.resume)
    if problems:
        for problem in problems:
            out(f'Refused: {problem}')
        return EXIT_FAILED
    control = deps.control() if args.if_requested and not initial else None
    request = control.pending() if control else None

    if args.resume:
        run = state['run']
        out(f"Resuming run {run['id']}: promoting {run['generation']}")
        try:
            promote(sql, names, int(run['id']), run['generation'])
        except (Failure, MirrorError) as error:
            fail_run(sql, names, int(run['id']), f'promotion failed: {error}')
            out(f'reference_import_failed: run {run["id"]}: {error}')
            return EXIT_FAILED
        out(f"Done: {names.serving} serves {run['generation']}.")
        return finish(deps, run, request, control, [])

    # Every run records the volume capacity, so the probe's disk figure follows an expansion.
    mirror.ensure_meta(sql, names)  # idempotent; creates mdp_meta on an empty server
    total = sql.volume_total()
    mirror.record_volume(sql, names, total)
    upstream = deps.latest()
    serving = None if initial else state['serving']['generation']
    age = (datetime.now(timezone.utc) - datetime.strptime(upstream, '%Y%m%d-%H%M%S').replace(tzinfo=timezone.utc)).days
    if age > STALE_DAYS:
        out(f'reference_dump_stale: upstream LATEST {upstream} is {age} days old')
    if not initial and serving is not None and upstream <= serving and not args.force and request is None:
        out(f'Nothing to do: {names.serving} serves {serving}; upstream LATEST is {upstream}.')
        return 0
    reason = ('initial import' if initial else 'forced' if args.force and upstream <= serving
              else f're-import requested at {request}' if request is not None and upstream <= serving
              else f'newer than {serving}')

    verdict = disk_verdict(int(state['used_bytes']), int(state['serving_bytes'] or 0), total, refresh=not initial)
    if verdict:
        out(verdict)
        return EXIT_DISK

    target = names.serving if initial else names.next
    run_id = start_run(sql, names, upstream, target)
    name = f'{MACHINE_PREFIX}{run_id}'
    out(f'Run {run_id}: importing {upstream} into {target} ({reason}); databases use {pct(int(state["used_bytes"]) / total)}.')
    volume_ids: list[str] = []
    try:
        volume_ids.append(deps.fly.create_volume(args.volume_gb))
        note_run(sql, names, run_id, 'provision', f'temporary volume {volume_ids[0]}, machine {name}')
        _, vm = importer_config()
        deps.fly.run_machine(name, volume_ids[0], machine_env(names, run_id, initial), vm, run_id)
        run = wait_for(deps, run_id, name, 'promoted' if initial else 'validated',
                       args.timeout_hours * 3600, args.poll_seconds)
        if not initial:
            out(f"Promoting {run['generation']}: {names.next} -> {names.serving}")
            promote(sql, names, run_id, run['generation'])
    except (Failure, MirrorError) as error:
        fail_run(sql, names, run_id, str(error))
        out(f'reference_import_failed: run {run_id}: {error}. {name} and its volume stay for inspection; '
            'see ops/runbooks/reference_import_failed.md.')
        return EXIT_FAILED
    out(f"Done: {names.serving} serves {run['generation']} (run {run_id}).")
    return finish(deps, run, request, control, volume_ids)


def discard_prior(deps: Deps) -> int:
    """Clear a failed or abandoned attempt: its run row, musicbrainz_next/_prev, machine and volume."""
    names, sql, out = deps.names, deps.sql, deps.out
    machines = [m for m in deps.fly.machines() if str(key(m, 'name') or '').startswith(MACHINE_PREFIX)]
    live = [key(m, 'name') for m in machines if key(m, 'state') in LIVE_STATES]
    if live:
        out(f'Refused: importer machine(s) {live} are still running; wait for them or stop them first.')
        return EXIT_FAILED
    run = mirror.status(sql, names)['run']
    if run and run['state'] in ('running', 'validated'):
        fail_run(sql, names, int(run['id']), 'reference_import_failed: discarded by operator (--discard-prior)')
        out(f"Marked run {run['id']} failed.")
    drop_attempt_databases(sql, names)
    reopen_serving(sql, names)
    out(f'Dropped {names.next} and {names.prev} if present; {names.serving} accepts connections.')
    for machine in machines:
        deps.fly.destroy_machine(key(machine, 'id'))
        out(f"Destroyed machine {key(machine, 'name')}.")
    for volume in deps.fly.volumes():
        if key(volume, 'name') == TEMP_VOLUME and key(volume, 'state') not in ('destroyed', 'destroying'):
            destroy_volume(deps, key(volume, 'id'))
            out(f"Destroyed volume {key(volume, 'id')}.")
    return 0


def show_status(deps: Deps) -> int:
    state = mirror.status(deps.sql, deps.names)
    total = state['volume_bytes']
    state['disk'] = pct(int(state['used_bytes']) / total) if total else 'unknown'
    try:
        state['upstream_latest'] = deps.latest()
    except (OSError, Failure) as error:
        state['upstream_latest'] = f'unreadable: {error}'
    deps.out(json.dumps(state, indent=2, default=str))
    return 0


def plan(args: argparse.Namespace, names: Names) -> list[str]:
    """The steps a run would take, built from the same argument lists the real run uses."""
    target = names.serving if args.initial else names.next
    env = machine_env(names, '<run>', args.initial)
    _, vm = importer_config()
    wrap = 'python3 ops/fly/mb-db/fly.py'
    steps = [(f'read {LATEST_URL}; over `{wrap} ssh console --app {mirror.DB_APP} --machine {mirror.DB_MACHINE}` '
              f'read the serving generation in {names.serving}, the newest import_run in {names.meta} '
              'and database sizes')]
    if args.resume:
        steps.append('promote the validated run with promote.sql, then destroy its machine and volume')
    else:
        if args.if_requested:
            steps.append('read control.reference_source.reimport_requested_at for musicbrainz via MDP_CONTROL_RT_URL')
        steps += [
            ('stop with exit 0 when LATEST is not newer than the serving generation, unless --force '
             'or a pending request'),
            (f'record df capacity in {names.meta}.volume; exit 3 with reference_disk_high when databases use '
             f'>= {pct(DISK_LIMIT)} of it'
             + ('' if args.initial else f', or would with a second generation (serving x{GROWTH:.2f})')),
            f'insert import_run (state running, target {target})',
            f'{wrap} ' + ' '.join(volume_create_args(args.volume_gb)),
            f'(cd ops/fly) {wrap} ' + ' '.join(machine_run_args(f'{MACHINE_PREFIX}<run>', '<volume>', env, vm, '<run>')),
            (f'poll every {args.poll_seconds:.0f} s for up to {args.timeout_hours:g} h until import_run is '
             + ('promoted' if args.initial else 'validated')),
        ]
        if not args.initial:
            steps.append(f'promote.sql: {names.next} -> {names.serving}, then drop {names.prev}')
        steps.append(f'{wrap} machine destroy <machine> --force; {wrap} volumes destroy <volume> --yes')
    if args.if_requested:
        steps.append('clear the served re-import request')
    mode = 'initial' if args.initial else 'resume' if args.resume else 'refresh'
    return [f'Dry run ({mode}): nothing remote runs. The run would:'] + [f'  {n}. {step}' for n, step in enumerate(steps, 1)]


def parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--dry-run', action='store_true', help='print the plan; run nothing remote')
    action = parser.add_mutually_exclusive_group()
    action.add_argument('--status', action='store_true', help='print mirror state and exit')
    action.add_argument('--discard-prior', action='store_true',
                        help='fail the abandoned run, drop musicbrainz_next/_prev, destroy refresh machines and volumes')
    action.add_argument('--resume', action='store_true', help='promote the newest validated run')
    parser.add_argument('--force', action='store_true', help='re-import even when upstream is not newer')
    parser.add_argument('--if-requested', action='store_true',
                        help='also re-import when the Reference page requested it; clear the request after promotion')
    parser.add_argument('--initial', action='store_true', help='load an empty server into musicbrainz_db')
    parser.add_argument('--volume-gb', type=int, default=60)
    parser.add_argument('--timeout-hours', type=float, default=24)
    parser.add_argument('--poll-seconds', type=float, default=300)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None, deps: Deps | None = None) -> int:
    args = parse_args(argv)
    if args.dry_run:
        for line in plan(args, deps.names if deps else Names()):
            (deps.out if deps else print)(line)
        return 0
    deps = deps or Deps()
    try:
        if args.status:
            return show_status(deps)
        if args.discard_prior:
            return discard_prior(deps)
        return refresh(args, deps)
    except (Failure, MirrorError) as error:
        deps.out(f'Refused: {error}')
        return EXIT_FAILED


if __name__ == '__main__':
    sys.exit(main())

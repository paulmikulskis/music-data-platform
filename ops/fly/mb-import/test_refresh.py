"""Local tests for the mirror refresh path against a disposable PostgreSQL.

    uv run --project functions pytest ops/fly/mb-import/test_refresh.py

The server defaults to 127.0.0.1:5444 as postgres/postgres (MDP_MB_TEST_PG{HOST,PORT,USER,PASSWORD}
override it). Tests create only mbt_* databases and drop them, and they restore the cluster-wide
mb_reader role (password, settings, pg_read_all_stats membership) when the session ends. import.sh
runs for real with stub fetch-dump.sh, createdb.sh, carton and wget that load the MusicBrainz
fixture instead of an upstream export. No test calls Fly or the network.
"""
from __future__ import annotations

import importlib.util
import os
import subprocess
import sys
import uuid
from pathlib import Path

import psycopg
import pytest

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
sys.path[:0] = [str(HERE), str(ROOT / 'ops/fly/mb-db')]
import mirror
import refresh

_spec = importlib.util.spec_from_file_location('apply_mdp_schema', ROOT / 'ops/fly/mb-db/apply-mdp-schema.py')
apply_mdp_schema = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(apply_mdp_schema)

FIXTURE = ROOT / 'functions/tests/fixtures/mb_mirror.sql'
GEN_A, GEN_B = '20260923-002121', '20261003-001512'
# Counts cover the full mb_mirror.sql tables, before selecting the tracked catalog's closure.
# Keep these literal so a fixture edit must explain its effect on generation validation.
FIXTURE_COUNTS = {
    'recording': 6,       # Distinct recordings; redirect ids are in a separate table.
    'isrc': 3,            # Recording-to-ISRC rows, not every recording has one.
    'url': 8,             # Two songs, two albums, one artist, Discogs, Wikidata and Instagram.
    'l_recording_url': 2, # Song URL links, one Spotify and one Apple.
    'l_release_url': 3,   # Two album links and one Discogs release link.
    'l_artist_url': 3,    # One artist's Spotify, Wikidata and Instagram links.
    'release': 3,         # Releases, each with one medium.
    'track': 6,           # Two track slots on each of the three media.
    'artist': 3,          # Credited artists, separate from their label affiliations.
    'artist_credit': 3,   # One single-artist credit per artist.
}
PG = {'PGHOST': os.environ.get('MDP_MB_TEST_PGHOST', '127.0.0.1'),
      'PGPORT': os.environ.get('MDP_MB_TEST_PGPORT', '5444'),
      'PGUSER': os.environ.get('MDP_MB_TEST_PGUSER', 'postgres'),
      'PGPASSWORD': os.environ.get('MDP_MB_TEST_PGPASSWORD', 'postgres')}
os.environ.update(PG)
READER_PASSWORD = 'mb_reader'  # the fixture's; finalize re-sets the same one

STUBS = {
    'fetch-dump.sh': 'echo "$STUB_LATEST" > "$MDP_DUMP_DIR/LATEST"',
    'wget': 'printf %s "$STUB_LATEST"',
    # carton exec -- database_exists MAINTENANCE: 0 when MDP_TARGET_DB exists, 1 when it does not.
    'carton': '[[ -z $(psql -X -A -t -d postgres -c "SELECT 1 FROM pg_database WHERE datname = \'$MDP_TARGET_DB\'") ]] && exit 1; exit 0',
    'createdb.sh': (
        'psql -X -q -v ON_ERROR_STOP=1 -d postgres -c "CREATE DATABASE $MDP_TARGET_DB"\n'
        'psql -X -q -v ON_ERROR_STOP=1 -d "$MDP_TARGET_DB" -f "$STUB_FIXTURE"\n'
        'if [[ ${STUB_BREAK:-0} == 1 ]]; then psql -X -q -d "$MDP_TARGET_DB" -c "DELETE FROM musicbrainz.isrc"; fi'),
}


def q(db: str, sql: str) -> str:
    result = subprocess.run(['psql', '-X', '-q', '-A', '-t', '-v', 'ON_ERROR_STOP=1', '-d', db, '-c', sql],
                            capture_output=True, text=True, check=False)
    assert result.returncode == 0, result.stderr
    return result.stdout.strip()


def reader(db: str) -> psycopg.Connection:
    return psycopg.connect(host=PG['PGHOST'], port=PG['PGPORT'], dbname=db, user='mb_reader',
                           password=READER_PASSWORD, autocommit=True)


def exists(db: str) -> bool:
    return q('postgres', f"SELECT count(*) FROM pg_database WHERE datname = '{db}'") == '1'


@pytest.fixture(scope='session', autouse=True)
def server():
    try:
        psycopg.connect(host=PG['PGHOST'], port=PG['PGPORT'], dbname='postgres', user=PG['PGUSER'],
                        password=PG['PGPASSWORD'], connect_timeout=5).close()
    except psycopg.OperationalError as error:
        pytest.skip(f'local PostgreSQL unavailable: {error}')
    existed = q('postgres', "SELECT count(*) FROM pg_roles WHERE rolname = 'mb_reader'") == '1'
    if existed:
        password = q('postgres', "SELECT coalesce(rolpassword, '') FROM pg_authid WHERE rolname = 'mb_reader'")
        config = q('postgres', "SELECT coalesce(array_to_string(rolconfig, E'\\n'), '') FROM pg_roles WHERE rolname = 'mb_reader'")
        member = q('postgres', "SELECT pg_has_role('mb_reader', 'pg_read_all_stats', 'MEMBER')") == 't'
    yield
    if not existed:
        q('postgres', 'DROP ROLE IF EXISTS mb_reader')
        return
    restore = ['ALTER ROLE mb_reader RESET ALL']
    if password:
        restore.append(f"ALTER ROLE mb_reader PASSWORD '{password}'")
    for line in filter(None, config.split('\n')):
        name, value = line.split('=', 1)
        restore.append(f'ALTER ROLE mb_reader SET {name} = ' + (value if name == 'search_path' else f"'{value}'"))
    if not member:
        restore.append('REVOKE pg_read_all_stats FROM mb_reader')
    for statement in restore:
        q('postgres', statement)


@pytest.fixture(scope='session')
def stubs(tmp_path_factory):
    bin_dir = tmp_path_factory.mktemp('stub-bin')
    for name, body in STUBS.items():
        path = bin_dir / name
        path.write_text(f'#!/usr/bin/env bash\nset -euo pipefail\n{body}\n')
        path.chmod(0o755)
    return bin_dir


@pytest.fixture
def names():
    suffix = uuid.uuid4().hex[:8]
    names = mirror.Names(serving=f'mbt_db_{suffix}', next=f'mbt_next_{suffix}', prev=f'mbt_prev_{suffix}',
                         meta=f'mbt_meta_{suffix}')
    yield names
    for db in (names.serving, names.next, names.prev, names.meta, f'mbt_ctl_{suffix}'):
        q('postgres', f'DROP DATABASE IF EXISTS {db} WITH (FORCE)')


def import_env(names: mirror.Names, stubs: Path, dump_dir: Path, latest: str, **extra: str) -> dict[str, str]:
    """What the importer machine sees, pointed at the local server instead of mdp-mb-db.internal."""
    return {'MUSICBRAINZ_POSTGRES_SERVER': PG['PGHOST'], 'PGPORT': PG['PGPORT'], 'POSTGRES_USER': PG['PGUSER'],
            'POSTGRES_PASSWORD': PG['PGPASSWORD'], 'MB_READER_PASSWORD': READER_PASSWORD,
            'MUSICBRAINZ_DB_SCHEMA_SEQUENCE': '31', 'MUSICBRAINZ_BASE_DOWNLOAD_URL': 'http://stub.invalid',
            'MDP_SERVING_DB': names.serving, 'MDP_NEXT_DB': names.next, 'MDP_META_DB': names.meta,
            'MDP_DUMP_DIR': str(dump_dir), 'MDP_SQL_DIR': str(HERE), 'MDP_MB_SERVER_DIR': str(dump_dir.parent),
            'PATH': f"{stubs}:{os.environ['PATH']}", 'STUB_LATEST': latest, 'STUB_FIXTURE': str(FIXTURE),
            # The musicbrainz role's search_path on the mirror, so extension placement matches production.
            'PGOPTIONS': '-c search_path=musicbrainz,public'} | extra


def run_import(env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    full = {k: v for k, v in os.environ.items() if not k.startswith('MDP_')} | env
    return subprocess.run(['bash', str(HERE / 'import.sh')], env=full, capture_output=True, text=True, check=False)


def serving_generation(names: mirror.Names, generation: str = GEN_A) -> dict:
    """The live mirror before refreshes existed: an imported musicbrainz_db, handed off by apply-mdp-schema."""
    q('postgres', f'CREATE DATABASE {names.serving}')
    subprocess.run(['psql', '-X', '-q', '-v', 'ON_ERROR_STOP=1', '-d', names.serving, '-f', str(FIXTURE)],
                   check=True, capture_output=True)
    return apply_mdp_schema.handoff(mirror.LocalPsql(total_bytes=10**15), names, generation, 31, out=lambda _: None)


class FakeFly:
    """Fly as seen by refresh.py: run_machine runs import.sh locally and leaves a stopped machine."""

    def __init__(self, env: dict[str, str] | None = None, during_import=None):
        self.env, self.during_import, self.calls, self.results, self._machines = env or {}, during_import, [], [], []

    def create_volume(self, size_gb):
        self.calls.append(('volumes create', size_gb))
        return 'vol_test'

    def run_machine(self, name, volume_id, env, vm, run_id):
        self.calls.append(('machine run', name, env['MDP_IMPORT_MODE'], env['MDP_IMPORT_RUN_ID']))
        self._machines = [{'id': 'm_test', 'name': name, 'state': 'started', 'config': {'mounts': [{'volume': volume_id}]}}]
        if self.during_import:
            self.during_import()
        self.results.append(run_import(env | self.env))
        self._machines[0]['state'] = 'stopped'

    def machines(self):
        self.calls.append(('machine list',))
        return self._machines

    def machine(self, name):
        return next((m for m in self.machines() if m['name'] == name), None)

    def volumes(self):
        return [{'id': 'vol_test', 'name': refresh.TEMP_VOLUME, 'state': 'created'}]

    def stop_machine(self, machine_id):
        self.calls.append(('machine stop', machine_id))

    def destroy_machine(self, machine_id):
        self.calls.append(('machine destroy', machine_id))

    def destroy_volume(self, volume_id):
        self.calls.append(('volumes destroy', volume_id))


def make_deps(names, fly, latest, total_bytes=10**15, control=None, lines=None):
    return refresh.Deps(sql=mirror.LocalPsql(total_bytes=total_bytes), fly=fly, latest=lambda: latest,
                        control=control or (lambda: pytest.fail('control plane read without --if-requested')),
                        names=names, sleep=lambda _: None,
                        out=(lines.append if lines is not None else lambda _: None))


def newest_run(names):
    return mirror.status(mirror.LocalPsql(), names)['run']


# --- tests -----------------------------------------------------------------------------------------

def test_scripts_parse():
    for script in (HERE / 'import.sh',):
        assert subprocess.run(['bash', '-n', str(script)], check=False).returncode == 0
    assert '\\ir ' not in mirror.bundle(HERE / 'finalize.sql')
    assert 'CREATE TABLE IF NOT EXISTS mdp.generation' in mirror.bundle(HERE / 'finalize.sql')


def test_handoff_writes_validated_generation_with_counts_and_indexes(names):
    result = serving_generation(names)
    row = q(names.serving, "SELECT generation, export_date, replication_sequence, schema_sequence, "
                           "validated_at IS NOT NULL, validated_at >= imported_at FROM mdp.generation")
    assert row == f'{GEN_A}|2026-09-23 00:21:21+00|189207|31|t|t'
    counts = q(names.serving, 'SELECT counts FROM mdp.generation')
    assert __import__('json').loads(counts) == FIXTURE_COUNTS
    indexes = q(names.serving, "SELECT string_agg(c.relname, ',' ORDER BY c.relname) FROM pg_index x "
                               "JOIN pg_class c ON c.oid = x.indexrelid WHERE x.indisvalid AND c.relname LIKE '%mdp%'")
    assert indexes == 'artist_credit_idx_mdp_name_trgm,url_idx_mdp_tail_id'
    # mdp-schema.sql analyzes the url table after building the tail index.
    assert q(names.serving, "SELECT count(*) FROM pg_stats WHERE tablename = 'url_idx_mdp_tail_id'") == '1'
    assert q(names.serving, "SELECT n.nspname FROM pg_extension e JOIN pg_namespace n ON n.oid = e.extnamespace "
                            "WHERE e.extname = 'pg_trgm'") == 'public'
    run = result['run']
    assert (run['generation'], run['target_db'], run['state']) == (GEN_A, names.serving, 'promoted')
    assert result['volume_bytes'] == 10**15
    # Running the handoff again is harmless: same generation, no second ledger row.
    apply_mdp_schema.handoff(mirror.LocalPsql(total_bytes=10**15), names, GEN_A, 31, out=lambda _: None)
    assert q(names.meta, 'SELECT count(*) FROM import_run') == '1'
    assert q(names.serving, 'SELECT count(*) FROM mdp.generation WHERE validated_at IS NOT NULL') == '1'


def test_analyze_url_gives_the_tail_index_statistics_and_nothing_else(names):
    """The first live import was analyzed before its tail index existed; --analyze-url adds the index's
    statistics without re-running finalize."""
    serving_generation(names)
    q(names.serving, 'DROP INDEX musicbrainz.url_idx_mdp_tail_id; '
                     'CREATE INDEX url_idx_mdp_tail_id ON musicbrainz.url (mdp.url_tail_id(url))')
    stats = "SELECT count(*) FROM pg_stats WHERE tablename = 'url_idx_mdp_tail_id'"
    validated = 'SELECT validated_at FROM mdp.generation'
    before = q(names.serving, validated)
    assert q(names.serving, stats) == '0'
    apply_mdp_schema.analyze_url(mirror.LocalPsql(), names, out=lambda _: None)
    assert q(names.serving, stats) == '1'
    assert q(names.serving, validated) == before


def test_handoff_keeps_trigram_access_when_pg_trgm_lives_in_musicbrainz(names):
    """The first live finalize created pg_trgm under the musicbrainz role's search_path; the handoff
    revokes EXECUTE on every musicbrainz function, so the reader needs pg_trgm granted back."""
    q('postgres', f'CREATE DATABASE {names.serving}')
    subprocess.run(['psql', '-X', '-q', '-v', 'ON_ERROR_STOP=1', '-d', names.serving, '-f', str(FIXTURE)],
                   check=True, capture_output=True)
    q(names.serving, 'CREATE EXTENSION pg_trgm SCHEMA musicbrainz')
    apply_mdp_schema.handoff(mirror.LocalPsql(total_bytes=10**15), names, GEN_A, 31, out=lambda _: None)
    with reader(names.serving) as conn:
        assert conn.execute("SELECT id FROM recording WHERE name % 'Flowrs'").fetchone() == (102,)


def test_reader_reads_generation_meta_and_sizes_but_cannot_write(names):
    serving_generation(names)
    with reader(names.serving) as conn:
        assert conn.execute('SELECT generation FROM mdp.generation').fetchone() == (GEN_A,)
        assert conn.execute("SELECT mdp.url_tail_id('https://music.apple.com/us/album/twin-ep/1500000202')").fetchone() == ('1500000202',)
        # The trigram path mb_resolve uses runs as the reader, with the reader's search_path.
        assert conn.execute("SELECT id FROM recording WHERE name % 'Flowrs' ORDER BY similarity(name, 'Flowrs') DESC LIMIT 1").fetchone() == (102,)
        conn.execute('SET default_transaction_read_only = off')
        for statement in ("INSERT INTO mdp.generation (generation, export_date, replication_sequence, schema_sequence) VALUES ('x', now(), 1, 1)",
                          'UPDATE musicbrainz.recording SET name = name', 'CREATE TABLE public.x (i int)',
                          'CREATE TABLE mdp.x (i int)'):
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                conn.execute(statement)
    with reader(names.meta) as conn:
        assert conn.execute('SELECT state FROM import_run').fetchone() == ('promoted',)
        assert conn.execute('SELECT total_bytes FROM volume').fetchone() == (10**15,)
        total = conn.execute('SELECT sum(pg_database_size(datname)) FROM pg_database WHERE NOT datistemplate').fetchone()[0]
        assert total > 0
        conn.execute('SET default_transaction_read_only = off')
        for statement in ("INSERT INTO import_run (generation, target_db, state) VALUES ('x', 'x', 'running')",
                          'UPDATE volume SET total_bytes = 1', 'CREATE TABLE public.x (i int)'):
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                conn.execute(statement)


def test_refresh_imports_validates_and_promotes(names, stubs, tmp_path):
    serving_generation(names)
    live = reader(names.serving)  # an open reader session is terminated by the swap
    fly = FakeFly(import_env(names, stubs, tmp_path / 'dump', GEN_B))
    lines: list[str] = []
    code = refresh.main(['--poll-seconds', '0'], make_deps(names, fly, GEN_B, lines=lines))
    assert code == 0, '\n'.join(lines) + fly.results[0].stdout[-3000:] + fly.results[0].stderr[-2000:]
    assert fly.results[0].returncode == 0
    assert [c[0] for c in fly.calls if c[0] != 'machine list'] == ['volumes create', 'machine run', 'machine destroy', 'volumes destroy']
    assert fly.calls[1][2:] == ('refresh', str(newest_run(names)['id']))
    run = newest_run(names)
    assert (run['generation'], run['target_db'], run['state'], run['phase']) == (GEN_B, names.next, 'promoted', 'done')
    assert run['finished_at'] and 'destroyed' in run['message']
    assert not exists(names.next) and not exists(names.prev)
    with pytest.raises(psycopg.OperationalError):
        live.execute('SELECT 1')
    with reader(names.serving) as conn:  # the reader URL's database now holds the new generation
        generation, validated = conn.execute('SELECT generation, validated_at IS NOT NULL FROM mdp.generation').fetchone()
        assert (generation, validated) == (GEN_B, True)
    phases = [line.split()[1] for line in (tmp_path / 'dump/mdp-import.log').read_text().splitlines()
              if line.startswith('MDP_IMPORT_PHASE')]
    assert phases == ['preflight', 'fetch', 'import', 'finalize', 'verify']
    assert 'ALTER ROLE' not in (tmp_path / 'dump/mdp-import.log').read_text()


def test_requested_reimport_runs_and_clears_only_the_served_request(names, stubs, tmp_path):
    serving_generation(names)
    ctl = names.meta.replace('mbt_meta_', 'mbt_ctl_')
    q('postgres', f'CREATE DATABASE {ctl}')
    q(ctl, 'CREATE SCHEMA control; CREATE TABLE control.reference_source (source text PRIMARY KEY, '
           'reimport_requested_at timestamptz, reimport_requested_by text, updated_at timestamptz NOT NULL DEFAULT now()); '
           "INSERT INTO control.reference_source VALUES ('musicbrainz', now() - interval '1 hour', 'operator@example.invalid', now())")
    url = f"postgresql://{PG['PGUSER']}:{PG['PGPASSWORD']}@{PG['PGHOST']}:{PG['PGPORT']}/{ctl}"
    control = lambda: refresh.ControlRequests(url)
    fly = FakeFly(import_env(names, stubs, tmp_path / 'dump', GEN_A))
    # Upstream equals the serving generation: without a request there is nothing to do.
    assert refresh.main(['--if-requested'], make_deps(names, FakeFly(), GEN_A, control=lambda: type(
        'NoRequest', (), {'pending': lambda self: None})())) == 0
    assert newest_run(names)['target_db'] == names.serving
    lines: list[str] = []
    code = refresh.main(['--if-requested', '--poll-seconds', '0'], make_deps(names, fly, GEN_A, control=control, lines=lines))
    assert code == 0, '\n'.join(lines)
    assert newest_run(names)['state'] == 'promoted'
    assert q(ctl, 'SELECT reimport_requested_at IS NULL, reimport_requested_by IS NULL FROM control.reference_source') == 't|t'

    # A request made while a refresh runs outlives that refresh.
    q(ctl, "UPDATE control.reference_source SET reimport_requested_at = now() - interval '1 minute'")
    later = lambda: q(ctl, "UPDATE control.reference_source SET reimport_requested_at = now() + interval '1 minute'")
    fly = FakeFly(import_env(names, stubs, tmp_path / 'dump2', GEN_A), during_import=later)
    assert refresh.main(['--if-requested', '--poll-seconds', '0'], make_deps(names, fly, GEN_A, control=control)) == 0
    assert q(ctl, 'SELECT reimport_requested_at IS NOT NULL FROM control.reference_source') == 't'


def test_failed_verify_leaves_generation_unvalidated_and_blocks_promotion(names, stubs, tmp_path):
    serving_generation(names)
    fly = FakeFly(import_env(names, stubs, tmp_path / 'dump', GEN_B, STUB_BREAK='1'))
    lines: list[str] = []
    assert refresh.main(['--poll-seconds', '0'], make_deps(names, fly, GEN_B, lines=lines)) == refresh.EXIT_FAILED
    assert fly.results[0].returncode != 0
    run = newest_run(names)
    assert (run['state'], run['phase']) == ('failed', 'verify')
    assert run['message'].startswith('reference_import_failed: verify exited')
    assert q(names.next, 'SELECT generation, validated_at IS NULL FROM mdp.generation') == f'{GEN_B}|t'
    assert 'required identity table is empty: isrc' in (tmp_path / 'dump/mdp-import.log').read_text()
    assert [c[0] for c in fly.calls if c[0] != 'machine list'] == ['volumes create', 'machine run']  # kept for inspection

    sql = mirror.LocalPsql()
    with pytest.raises(mirror.MirrorError, match='is not a validated import'):
        refresh.promote(sql, names, run['id'], GEN_B)
    # Even a ledger that claims validation cannot promote a database whose generation is not validated.
    q(names.meta, f"UPDATE import_run SET state = 'validated' WHERE id = {run['id']}")
    with pytest.raises(mirror.MirrorError, match='does not hold validated generation'):
        refresh.promote(sql, names, run['id'], GEN_B)
    q(names.meta, f"UPDATE import_run SET state = 'failed' WHERE id = {run['id']}")
    with reader(names.serving) as conn:
        assert conn.execute('SELECT generation FROM mdp.generation').fetchone() == (GEN_A,)
    assert exists(names.next)

    # The next scheduled run refuses until an operator discards the attempt.
    lines = []
    assert refresh.main([], make_deps(names, FakeFly(), GEN_B, lines=lines)) == refresh.EXIT_FAILED
    assert any(names.next in line and '--discard-prior' in line for line in lines)
    discard = FakeFly()
    discard._machines = [{'id': 'm_old', 'name': 'mdp-refresh-9', 'state': 'stopped', 'config': {}}]
    assert refresh.main(['--discard-prior'], make_deps(names, discard, GEN_B)) == 0
    assert ('machine destroy', 'm_old') in discard.calls and ('volumes destroy', 'vol_test') in discard.calls
    assert not exists(names.next) and q(names.serving, 'SELECT generation FROM mdp.generation') == GEN_A


def test_disk_verdict_thresholds():
    assert refresh.disk_verdict(80, 0, 100).startswith('reference_disk_high: databases use 80.0%')
    assert refresh.disk_verdict(79, 0, 100) is None
    assert refresh.disk_verdict(30, 30, 100) is None  # 30 + 33 = 63%
    assert refresh.disk_verdict(40, 40, 100).startswith('reference_disk_high: a second generation')  # 84%
    assert refresh.disk_verdict(40, 40, 100, refresh=False) is None
    assert refresh.disk_verdict(1, 0, 0).startswith('reference_disk_high')
    # Today's mirror: 59 GB serving on the 250 GB volume (about 233 GiB usable) projects to about 50%.
    assert refresh.disk_verdict(59 * 10**9, 59 * 10**9, 233 * 2**30) is None
    assert refresh.disk_verdict(96 * 10**9, 96 * 10**9, 233 * 2**30).startswith('reference_disk_high: a second')


def test_refresh_refuses_on_high_disk_before_touching_fly(names):
    serving_generation(names)
    used = int(mirror.status(mirror.LocalPsql(), names)['used_bytes'])
    fly, lines = FakeFly(), []
    code = refresh.main([], make_deps(names, fly, GEN_B, total_bytes=int(used / 0.95), lines=lines))
    assert code == refresh.EXIT_DISK and lines[-1].startswith('reference_disk_high: databases use')
    assert fly.calls == []
    assert newest_run(names)['target_db'] == names.serving  # no import_run was started
    assert q(names.meta, 'SELECT total_bytes FROM volume') == str(int(used / 0.95))


def test_nothing_to_do_when_upstream_is_not_newer(names):
    serving_generation(names)
    fly, lines = FakeFly(), []
    assert refresh.main([], make_deps(names, fly, GEN_A, lines=lines)) == 0
    assert fly.calls == [] and lines[-1].startswith('Nothing to do')
    assert q(names.meta, 'SELECT count(*) FROM import_run') == '1'


def test_dry_run_prints_plan_without_calling_fly(monkeypatch, capsys):
    def forbidden(*args, **kwargs):
        raise AssertionError('dry run touched a remote')
    monkeypatch.setattr(refresh.fly, 'run', forbidden)
    monkeypatch.setattr(refresh.urllib.request, 'urlopen', forbidden)
    monkeypatch.setattr(refresh.mirror, 'FlySsh', forbidden)
    assert refresh.main(['--dry-run', '--if-requested']) == 0
    plan = capsys.readouterr().out
    for expected in ('volumes create mbrefresh --app mdp-mb-import', 'machine run . --dockerfile mb-import/Dockerfile',
                     '--volume <volume>:/media/dbdump', '--env MDP_IMPORT_MODE=refresh', 'promote.sql: musicbrainz_next -> musicbrainz_db',
                     'reference_disk_high', 'clear the served re-import request'):
        assert expected in plan


def test_initial_import_serves_when_validated(names, stubs, tmp_path):
    env = import_env(names, stubs, tmp_path / 'dump', GEN_A, MDP_IMPORT_MODE='initial')
    result = run_import(env)
    assert result.returncode == 0, result.stdout[-3000:] + result.stderr[-2000:]
    run = newest_run(names)
    assert (run['generation'], run['target_db'], run['state'], run['phase']) == (GEN_A, names.serving, 'promoted', 'serving')
    assert q(names.serving, 'SELECT validated_at IS NOT NULL FROM mdp.generation') == 't'
    # A second attempt on the same volume stops at the prior-attempt marker and changes nothing.
    again = run_import(env)
    assert again.returncode == 1 and 'prior attempt exists' in again.stdout
    # A fresh volume still refuses to load over an existing database.
    again = run_import(import_env(names, stubs, tmp_path / 'dump2', GEN_A, MDP_IMPORT_MODE='initial'))
    assert again.returncode == 1 and 'already exists' in (tmp_path / 'dump2/mdp-import.log').read_text()
    assert q(names.meta, "SELECT state FROM import_run ORDER BY id DESC LIMIT 1") == 'failed'


def test_interrupted_promotion_rolls_back_and_reopens_serving(names, stubs, tmp_path):
    serving_generation(names)
    run_id = refresh.start_run(mirror.LocalPsql(), names, GEN_B, names.next)
    result = run_import(import_env(names, stubs, tmp_path / 'dump', GEN_B, MDP_IMPORT_MODE='refresh',
                                   MDP_IMPORT_RUN_ID=str(run_id)))
    assert result.returncode == 0, result.stdout[-3000:]
    assert newest_run(names)['state'] == 'validated'
    # Fail the swap after both renames ran inside it: the whole swap must roll back.
    q(names.meta, "CREATE FUNCTION public.block() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN "
                  "RAISE EXCEPTION 'injected'; END $$; CREATE TRIGGER block BEFORE UPDATE ON import_run "
                  "FOR EACH ROW WHEN (NEW.state = 'promoted') EXECUTE FUNCTION public.block()")
    with pytest.raises(mirror.MirrorError, match='did not commit'):
        refresh.promote(mirror.LocalPsql(), names, run_id, GEN_B)
    assert q('postgres', f"SELECT datallowconn FROM pg_database WHERE datname = '{names.serving}'") == 't'
    assert exists(names.next) and not exists(names.prev)
    with reader(names.serving) as conn:
        assert conn.execute('SELECT generation FROM mdp.generation').fetchone() == (GEN_A,)
    q(names.meta, 'DROP TRIGGER block ON import_run')
    refresh.promote(mirror.LocalPsql(), names, run_id, GEN_B)
    with reader(names.serving) as conn:
        assert conn.execute('SELECT generation FROM mdp.generation').fetchone() == (GEN_B,)
    assert not exists(names.next) and not exists(names.prev)


def test_fly_calls_go_through_the_guarded_wrapper(monkeypatch):
    seen = []

    def fake_run(args, **kwargs):
        seen.append((args, kwargs))
        out = {'volumes': '{"id": "vol_new", "name": "mbrefresh"}', 'machine': 'No machines are available on this app',
               'ssh': 'noise\nMDP_JSON {"ok": 1}\n'}[args[0]]
        return subprocess.CompletedProcess(args, 0, stdout=out, stderr='')
    monkeypatch.setattr(refresh.fly, 'run', fake_run)
    ops = refresh.FlyOps()
    assert ops.create_volume(60) == 'vol_new'
    assert ops.machines() == []
    env, vm = refresh.importer_config()
    ops.run_machine('mdp-refresh-7', 'vol_new', env, vm, 7)
    assert mirror.records(mirror.FlySsh().psql('mdp_meta', 'SELECT 1;')) == {'ok': 1}
    for args, _ in seen:
        assert args[-2:] == ['--org', 'example-org'] or args[args.index('--org') + 1] == 'example-org'
    run_args, run_kwargs = seen[2]
    assert run_args[:5] == ['machine', 'run', '.', '--dockerfile', 'mb-import/Dockerfile']
    assert run_kwargs['cwd'] == ROOT / 'ops/fly' and (ROOT / 'ops/fly' / 'mb-import/Dockerfile').exists()
    assert (vm['size'], vm['memory_mb']) == ('performance-4x', 16384)
    ssh_args, ssh_kwargs = seen[3]
    assert ssh_args[:3] == ['ssh', 'console', '--app'] and ssh_args[3] == 'mdp-mb-db'
    assert ssh_args[-1] == 'psql -X -q -A -t -v ON_ERROR_STOP=1 -U musicbrainz -d mdp_meta -f -'
    assert ssh_kwargs['input'] == 'SELECT 1;'

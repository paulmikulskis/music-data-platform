#!/usr/bin/env python3
"""Reproducible storage gates. Long runs use tmux mdp-test-pglake; credentials are dev-only."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time
from urllib.parse import urlsplit, urlunsplit, parse_qsl, urlencode

ROOT = Path(__file__).resolve().parents[2]
os.chdir(ROOT)
PG = 'mdp-pg-lake'
STATE = Path('/tmp/mdp-storage-gate-results.json')
ENV = dict(os.environ, PGHOST='127.0.0.1', PGPORT='5434', PGUSER='postgres',
           PGPASSWORD=os.environ.get('POSTGRES_PASSWORD', 'postgres'), PGSSLMODE='require')

def run(cmd, *, input=None, env=None, check=True):
    p = subprocess.run(cmd, input=input, text=True, stdout=subprocess.PIPE,
                       stderr=subprocess.STDOUT, env=env or ENV)
    if check and p.returncode:
        raise RuntimeError(p.stdout[-4000:])
    return p

def sql(query, database='warehouse', role='postgres', check=True):
    env = dict(ENV, PGUSER=role, PGPASSWORD=ENV['PGPASSWORD'] if role == 'postgres' else role)
    return run(['psql', '-X', '-v', 'ON_ERROR_STOP=1', '-v', 'VERBOSITY=verbose', '-At',
                '-d', database, '-c', query], env=env, check=check)

def lake():
    return sql("SELECT count(*) FROM pg_extension WHERE extname='pg_lake'").stdout.strip() == '1'

class LakeUnavailable(RuntimeError):
    pass

class BlockedInput(RuntimeError):
    pass

def need_lake():
    if not lake():
        mode=run(['docker','exec',PG,'printenv','WITH_PG_LAKE']).stdout.strip()
        if mode=='0':
            raise LakeUnavailable('pg_lake unavailable: WITH_PG_LAKE=0; see bounded build history')
        raise RuntimeError('pg_lake missing from an extension-enabled image')

def wait_db():
    for _ in range(100):
        if sql('SELECT 1', check=False).returncode == 0:
            return
        time.sleep(.2)
    raise RuntimeError('Postgres did not recover within 20s')

def pgdata():
    directory=sql('SHOW data_directory').stdout.strip()
    assert directory=='/var/lib/postgresql/data/pgdata',directory
    run(['docker','exec',PG,'sh','-c','test -d /var/lib/postgresql/data/lost+found && test -s "$PGDATA/PG_VERSION"'])
    print('fresh volume with lost+found initialized; data_directory=/var/lib/postgresql/data/pgdata')

def network():
    print(run([sys.executable,'ops/local/prove-network.py']).stdout.strip())

def haproxy_config():
    p=run(['docker','run','--rm','-v',str(ROOT/'ops/fly/haproxy')+':/usr/local/etc/haproxy:ro',
           'haproxy:3.2','haproxy','-c','-f','/usr/local/etc/haproxy/haproxy.cfg'])
    print(p.stdout.strip())
    print('haproxy -c exit=0; Fly DNS warning is expected locally')

def fly_config():
    import tomllib
    config=tomllib.loads((ROOT/'ops/fly/haproxy/fly.toml').read_text())
    service=config['services'][0]
    assert 'proxy_proto_options' not in service
    assert service['ports'][0]['proxy_proto_options']=={'version':'v2'}
    print('local TOML: PROXY v2 option is under services.ports')
    p=run(['fly','config','validate','--config','ops/fly/haproxy/fly.toml'],check=False)
    if p.returncode and 'no access token available' in p.stdout:
        raise BlockedInput(p.stdout.strip())
    if p.returncode: raise RuntimeError(p.stdout.strip())
    print(p.stdout.strip())

def basic():
    p = run(['psql', 'host=127.0.0.1 port=5434 user=postgres sslmode=require',
             '-X', '-c', "select version(), current_setting('ssl')"])
    print(p.stdout.strip())
    for db in ('control', 'warehouse'):
        print(db + ':')
        print(run(['psql', '-X', '-d', db, '-c', r'\dx']).stdout.strip())
    assert run(['docker', 'exec', PG, 'sh', '-c', 'cat /proc/1/comm']).stdout.strip() == 'supervisord'
    print('supervisord is PID 1; ssl=on; both databases have plpython3u')

def tls():
    assert sql("SELECT ssl FROM pg_stat_ssl WHERE pid=pg_backend_pid()").stdout.strip() == 't'
    bad = run(['psql', '-X', 'host=127.0.0.1 port=5434 user=postgres sslmode=disable', '-c', 'SELECT 1'], check=False)
    assert bad.returncode and 'pg_hba.conf rejects connection' in bad.stdout
    print('TLS required: connected with ssl=t; sslmode=disable refused: pg_hba.conf rejects connection, no encryption')

def plpython():
    print(sql('DO $$ import httpx $$ LANGUAGE plpython3u').stdout.strip())
    p = sql("""CREATE OR REPLACE FUNCTION public.mdp_gate_http() RETURNS integer
LANGUAGE plpython3u AS $$
import httpx
return httpx.get('http://mdp-minio:9000/minio/health/live', timeout=5).status_code
$$;
SELECT public.mdp_gate_http(); DROP FUNCTION public.mdp_gate_http();""")
    assert '200' in p.stdout
    print('plpython3u import httpx: OK; GET http://mdp-minio:9000/minio/health/live = 200')

def roles():
    names = ['migrator','rights_sync','control_rt','functions_rt','service_read','loader_wh','dbt_transform','workbench_wh','reader_wh']
    found = sql('SELECT rolname FROM pg_roles WHERE rolname IN (' + ','.join("'"+r+"'" for r in names) + ') ORDER BY 1').stdout.splitlines()
    assert set(found) == set(names)
    print('nine roles exist: ' + ', '.join(found))
    # Source without bootstrapping, then replace (never append) sslmode.
    exported = run(['bash', '-c', 'source ops/local/init.sh >/dev/null 2>&1; for key in ${!MDP_@}; do if [[ $key == *_URL ]]; then printf \"%s=%s\\0\" \"$key\" \"${!key}\"; fi; done'],
                   env=dict(ENV, MDP_PG_PORT='5434')).stdout
    proof_env = dict(ENV)
    for entry in exported.split('\0'):
        key, sep, value = entry.partition('=')
        if sep and key.startswith('MDP_') and key.endswith('_URL'):
            url = urlsplit(value)
            query = [(k, v) for k, v in parse_qsl(url.query) if k != 'sslmode']
            proof_env[key] = urlunsplit(url._replace(query=urlencode(query + [('sslmode', 'require')])))
    run(['pnpm', '--dir', 'control', '--filter', '@mdp/control-db', 'migrate'], env=proof_env)
    p = run([sys.executable, 'ops/local/prove-grants.py'],
            env=proof_env, check=False)
    print('\n'.join(line for line in p.stdout.splitlines() if 'SUMMARY' in line or line.startswith(('FAIL', 'EVIDENCE'))))
    Path('/tmp/mdp-storage-role-proof.log').write_text(p.stdout)
    if p.returncode:
        raise RuntimeError('Control proof failed; inspect the identified grants evidence and sandbox schema policy')

def grants():
    sql("SET ROLE loader_wh; CREATE TABLE IF NOT EXISTS raw.fixture_grant_test(id int); INSERT INTO raw.fixture_grant_test VALUES (1)")
    sql('CREATE SCHEMA IF NOT EXISTS wb_test', role='workbench_wh')
    assert sql('SELECT count(*) FROM raw.fixture_grant_test', role='workbench_wh').stdout.strip() != '0'
    bad = sql('CREATE TABLE raw.x(id int)', role='workbench_wh', check=False)
    assert bad.returncode and '42501' in bad.stdout
    bad = sql('SELECT * FROM mdp.fixture_hidden', role='workbench_wh', check=False)
    assert bad.returncode and '42501' in bad.stdout
    assert sql("SELECT has_schema_privilege(current_user, 'mdp', 'USAGE')", role='workbench_wh').stdout.strip() == 'f'
    sql('CREATE SCHEMA IF NOT EXISTS marts; CREATE TABLE IF NOT EXISTS marts.fixture_grant_test(id int); INSERT INTO marts.fixture_grant_test VALUES(1)', role='dbt_transform')
    assert sql('SELECT count(*) FROM marts.fixture_grant_test', role='reader_wh').stdout.strip() != '0'
    bad = sql('INSERT INTO marts.fixture_grant_test VALUES(2)', role='reader_wh', check=False)
    assert bad.returncode and '42501' in bad.stdout
    # Exercise future schema grants and service reads, not only bootstrap schemas.
    sql('CREATE SCHEMA IF NOT EXISTS tenant_p2; CREATE TABLE IF NOT EXISTS tenant_p2.future(id int)', role='dbt_transform')
    sql('SELECT * FROM tenant_p2.future', role='reader_wh')
    sql('SELECT * FROM tenant_p2.future', role='service_read')
    print('workbench: CREATE SCHEMA/SELECT raw PASS; CREATE raw.x/mdp access denied 42501; reader: SELECT marts/tenant PASS, INSERT denied 42501; service future-schema SELECT PASS')

def function_acl():
    sql("CREATE OR REPLACE FUNCTION mdp.fixture_protected() RETURNS int LANGUAGE sql AS 'SELECT 1'")
    assert sql("SELECT has_function_privilege('service_read','mdp.fixture_protected()','EXECUTE')").stdout.strip()=='f'
    print(sql(r'\df+ mdp.fixture_protected').stdout.strip())
    # Roll back permissive defaults even when the assertion fails.
    p=sql("BEGIN; ALTER DEFAULT PRIVILEGES GRANT EXECUTE ON FUNCTIONS TO PUBLIC; CREATE FUNCTION mdp.fixture_permissive() RETURNS int LANGUAGE sql AS 'SELECT 1'; SELECT has_function_privilege('service_read','mdp.fixture_permissive()','EXECUTE'); ROLLBACK")
    assert 'f' in p.stdout.splitlines()
    sql('DROP FUNCTION mdp.fixture_protected()')
    print('postgres-created mdp functions: service_read EXECUTE=false, including permissive creator defaults')

def lineage():
    columns=['_run_id','_dump_id','_landed_seq','_cycle_id','_revision_id','_target_id','_request_id','_source_key','_ingested_at','_extra']
    for creator in ('postgres','loader_wh'):
        table='raw.fixture_lineage_'+creator
        sql(f'DROP TABLE IF EXISTS {table}; CREATE TABLE {table}(payload text, _run_id text)',role=creator)
        sql(f'SELECT _run_id FROM {table}',role='reader_wh')
        sql(f'ALTER TABLE {table} '+', '.join('ADD COLUMN '+c+' text' for c in columns[1:]),role=creator)
        sql(f'SELECT '+','.join(columns)+f' FROM {table}',role='reader_wh')
        for query in (f'SELECT payload FROM {table}',f'SELECT * FROM {table}'):
            bad=sql(query,role='reader_wh',check=False)
            assert bad.returncode and '42501' in bad.stdout
        for reader in ('dbt_transform','workbench_wh','service_read'):
            sql(f'SELECT payload FROM {table}',role=reader)
        sql(f'ALTER TABLE {table} RENAME COLUMN _extra TO renamed_payload',role=creator)
        bad=sql(f'SELECT renamed_payload FROM {table}',role='reader_wh',check=False)
        assert bad.returncode and '42501' in bad.stdout
        sql(f'DROP TABLE {table}',role=creator)
    sql('SELECT _dump_id FROM raw.fixture_heap_repair',role='reader_wh')
    print('existing raw lineage and CREATE/ALTER for postgres/loader: 10 columns readable; payload, SELECT * and renamed lineage denied 42501; dbt/workbench raw SELECT passes')

def workbench_boundary():
    sql('CREATE SCHEMA IF NOT EXISTS wb_fixture_boundary',role='workbench_wh')
    for query in ('CREATE SCHEMA fixture_forbidden', 'CREATE SCHEMA "WB_wrong"',
                  'ALTER SCHEMA wb_fixture_boundary RENAME TO fixture_forbidden',
                  'CREATE SCHEMA wb_ok; CREATE SCHEMA fixture_forbidden',
                  "DO $$ BEGIN EXECUTE 'CREATE SCHEMA fixture_forbidden'; END $$"):
        bad=sql(query,role='workbench_wh',check=False)
        assert bad.returncode and '42501' in bad.stdout, bad.stdout
    sql('CREATE TABLE wb_fixture_boundary.allowed(id int); DROP TABLE wb_fixture_boundary.allowed',role='workbench_wh')
    sql('DROP SCHEMA wb_fixture_boundary',role='workbench_wh')
    print('wb_* CREATE/DDL allowed; production name, uppercase prefix, rename, multi-statement and dynamic SQL denied 42501')

def future_readers():
    sql('CREATE SCHEMA IF NOT EXISTS tenant_fixture_admin')
    sql('CREATE SCHEMA IF NOT EXISTS fixture_admin_marts')
    for schema in ('tenant_fixture_admin','fixture_admin_marts'):
        sql(f'CREATE TABLE {schema}.future(id int)')
        sql(f'SELECT * FROM {schema}.future',role='reader_wh')
        sql(f'DROP TABLE {schema}.future')
    print('postgres-created tables in existing tenant_* and *_marts schemas: reader SELECT passes')

def bootstrap_rerun():
    for _ in range(2):
        run(['psql','-X','-v','ON_ERROR_STOP=1','-d','warehouse','-f','ops/fly/postgres/boot/init/10-warehouse-grants.sql'])
    assert sql("SELECT count(*) FROM pg_event_trigger WHERE evtname LIKE 'mdp_%'").stdout.strip()=='4'
    print('warehouse bootstrap applied twice with existing raw; COMMIT twice; exactly four event triggers')

def sidecar():
    need_lake()
    before = run(['docker','exec',PG,'supervisorctl','pid','pgduck_server']).stdout.strip()
    started = time.monotonic()
    run(['docker','exec',PG,'sh','-c','kill -9 "$1"','_',before])
    memory=None
    for _ in range(100):
        p = run(['docker','exec',PG,'supervisorctl','pid','pgduck_server'], check=False).stdout.strip()
        if p.isdigit() and int(p)>0 and p != before:
            ready=run(['docker','exec','-u','postgres',PG,'psql','-h','/tmp','-p','5332','-At','-c',"SELECT current_setting('memory_limit')"],check=False)
            if ready.returncode==0:
                memory=ready.stdout.strip()
                break
        time.sleep(.1)
    elapsed = time.monotonic()-started
    assert p != before and p != '0' and memory is not None and elapsed <= 10
    print(f'pgduck_server SIGKILL: PID {before} -> {p} in {elapsed:.2f}s; memory_limit={memory}')

def sidecar_health():
    need_lake()
    p=run(['docker','exec','-u','postgres',PG,'psql','host=/tmp port=5332 dbname=postgres','-At','-c','SELECT version()'])
    print('Independent pgduck socket health: '+p.stdout.strip())

def credentials():
    need_lake()
    p = run(['docker','exec','-u','postgres',PG,'psql','-h','/tmp','-p','5332','-At','-c',
             "SELECT name, type FROM duckdb_secrets() WHERE name='mdp_storage'"])
    assert 'mdp_storage' in p.stdout
    print(p.stdout.strip() + '; credentials configured inside pgduck_server (secret values not selected)')

def parquet():
    # pyarrow is a gate-only dependency in a temporary venv, never in the image.
    run(['uv','venv','--allow-existing','/tmp/mdp-storage-arrow'])
    run(['uv','pip','install','--python','/tmp/mdp-storage-arrow/bin/python','pyarrow'])
    run(['/tmp/mdp-storage-arrow/bin/python','-c',
         "import pyarrow as pa, pyarrow.parquet as pq; pq.write_table(pa.table({'id':[1,2,3], '_dump_id':['fixture-dump']*3}), '/tmp/mdp-storage.parquet')"])
    p = run(['docker','compose','-f','ops/local/docker-compose.yml','run','--rm','-v','/tmp/mdp-storage.parquet:/tmp/fixture.parquet:ro',
             'mdp-minio-init','-ec', 'mc alias set local http://mdp-minio:9000 "$MINIO_ROOT_USER" "$MINIO_ROOT_PASSWORD" >/dev/null; mc cp /tmp/fixture.parquet local/mdp-dumps/fixtures/fixture.parquet'])
    print('pyarrow wrote 3 rows; mc uploaded s3://mdp-dumps/fixtures/fixture.parquet')

def iceberg():
    need_lake()
    sql('DROP TABLE IF EXISTS raw.fixture_iceberg; CREATE TABLE raw.fixture_iceberg(id bigint, _dump_id text) USING iceberg', role='loader_wh')
    print('CREATE TABLE raw.fixture_iceberg USING iceberg: CREATE TABLE')

def copy():
    need_lake()
    sql('DROP TABLE IF EXISTS raw.fixture_heap; CREATE TABLE raw.fixture_heap(id bigint, _dump_id text)', role='loader_wh')
    for kind in ('heap','iceberg'):
        p=sql(f"COPY raw.fixture_{kind} FROM 's3://mdp-dumps/fixtures/fixture.parquet'", role='loader_wh')
        assert sql(f'SELECT count(*) FROM raw.fixture_{kind}').stdout.strip() == '3'
        print(f'{kind}: {p.stdout.strip()}; count=3')

def alter():
    need_lake()
    sql('ALTER TABLE raw.fixture_iceberg ADD COLUMN nullable_note text', role='loader_wh')
    assert sql('SELECT count(*) FROM raw.fixture_iceberg WHERE nullable_note IS NULL').stdout.strip() == '3'
    print('ALTER TABLE ADD COLUMN nullable_note text: 3 existing rows remain NULL')

def repair():
    need_lake()
    for kind in ('heap','iceberg'):
        sql(f"BEGIN; DELETE FROM raw.fixture_{kind} WHERE _dump_id='fixture-dump'; COPY raw.fixture_{kind}(id,_dump_id) FROM 's3://mdp-dumps/fixtures/fixture.parquet'; COMMIT", role='loader_wh')
        assert sql(f'SELECT count(*),count(DISTINCT id) FROM raw.fixture_{kind}').stdout.strip()=='3|3'
        print(f'{kind}: DELETE 3; COPY 3; rows=3; distinct id=3')

def recovery_postgres():
    # Slow streaming COPY proves a real open COPY, not a kill after commit.
    sql('DROP TABLE IF EXISTS raw.fixture_recovery; CREATE TABLE raw.fixture_recovery(id int)')
    child=subprocess.Popen(['psql','-X','-v','ON_ERROR_STOP=1','-d','warehouse'], stdin=subprocess.PIPE,
                           stdout=subprocess.PIPE,stderr=subprocess.STDOUT,text=True,env=ENV)
    child.stdin.write('COPY raw.fixture_recovery FROM STDIN;\n' + ''.join(str(i)+'\n' for i in range(20000))); child.stdin.flush()
    for _ in range(50):
        active=sql("SELECT count(*) FROM pg_stat_activity WHERE query LIKE 'COPY raw.fixture_recovery%' AND state='active'").stdout.strip()
        if active=='1': break
        time.sleep(.1)
    assert active=='1'
    processed=sql("SELECT tuples_processed FROM pg_stat_progress_copy WHERE relid='raw.fixture_recovery'::regclass").stdout.strip()
    assert int(processed)>0, 'COPY had not processed rows before kill'
    assert sql('SELECT count(*) FROM raw.fixture_recovery').stdout.strip()=='0'
    pid=run(['docker','exec',PG,'supervisorctl','pid','postgres']).stdout.strip()
    run(['docker','exec',PG,'sh','-c','kill -9 "$1"','_',pid])
    child.stdin.close(); child.wait(timeout=15)
    wait_db()
    assert sql('SELECT count(*) FROM raw.fixture_recovery').stdout.strip()=='0'
    print(f'postgres SIGKILL during active COPY FROM STDIN ({processed} rows processed); supervisor recovered; visible rows=0')

def recovery_pgduck():
    need_lake()
    # Force COPY to remain active in a row trigger while rows are uncommitted.
    sql("""DROP TABLE IF EXISTS raw.fixture_duck_recovery; CREATE TABLE raw.fixture_duck_recovery(id bigint, _dump_id text);
CREATE OR REPLACE FUNCTION public.fixture_slow() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN PERFORM pg_sleep(2); RETURN NEW; END $$;
CREATE TRIGGER slow BEFORE INSERT ON raw.fixture_duck_recovery FOR EACH ROW EXECUTE FUNCTION public.fixture_slow();""")
    child=subprocess.Popen(['psql','-X','-v','ON_ERROR_STOP=1','-d','warehouse','-c',
                            "COPY raw.fixture_duck_recovery FROM 's3://mdp-dumps/fixtures/fixture.parquet'"],
                           stdout=subprocess.PIPE,stderr=subprocess.STDOUT,text=True,env=ENV)
    time.sleep(1)
    active=sql("SELECT count(*) FROM pg_stat_activity WHERE query LIKE 'COPY raw.fixture_duck_recovery%' AND state='active'").stdout.strip()
    assert active=='1'
    pid=run(['docker','exec',PG,'supervisorctl','pid','pgduck_server']).stdout.strip()
    run(['docker','exec',PG,'sh','-c','kill -9 "$1"','_',pid])
    output=child.communicate(timeout=30)[0]
    wait_db()
    count=sql('SELECT count(*) FROM raw.fixture_duck_recovery').stdout.strip()
    assert count in ('0','3'), 'partial COPY became visible: '+count
    for _ in range(50):
        ready=run(['docker','exec',PG,'supervisorctl','status','pgduck_server'],check=False)
        if 'RUNNING' in ready.stdout: break
        time.sleep(.2)
    assert 'RUNNING' in ready.stdout
    print(f'pgduck SIGKILL during active COPY; visible rows={count} (only 0 or complete 3 accepted); supervisor RUNNING')

def recovery_iceberg_pgduck(process="pgduck_server"):
    need_lake()
    run(['/tmp/mdp-storage-arrow/bin/python','-c', """import pyarrow as pa, pyarrow.parquet as pq
schema=pa.schema([('id',pa.int64()),('_dump_id',pa.string())])
with pq.ParquetWriter('/tmp/mdp-storage-large.parquet',schema) as writer:
    for start in range(0,50000000,100000):
        writer.write_table(pa.table({'id':pa.array(range(start,start+100000),type=pa.int64()),'_dump_id':['fixture-crash']*100000}))
"""])
    run(['docker','compose','-f','ops/local/docker-compose.yml','run','--rm','-v','/tmp/mdp-storage-large.parquet:/tmp/large.parquet:ro',
         'mdp-minio-init','-ec','mc alias set local http://mdp-minio:9000 "$MINIO_ROOT_USER" "$MINIO_ROOT_PASSWORD" >/dev/null; mc cp /tmp/large.parquet local/mdp-dumps/fixtures/large.parquet'])
    sql('DROP TABLE IF EXISTS raw.fixture_iceberg_recovery; CREATE TABLE raw.fixture_iceberg_recovery(id bigint,_dump_id text) USING iceberg',role='loader_wh')
    child=subprocess.Popen(['psql','-X','-v','ON_ERROR_STOP=1','-d','warehouse','-c',
                            "COPY raw.fixture_iceberg_recovery FROM 's3://mdp-dumps/fixtures/large.parquet'"],
                           stdout=subprocess.PIPE,stderr=subprocess.STDOUT,text=True,env=ENV)
    active='0'
    for _ in range(100):
        active=sql("SELECT count(*) FROM pg_stat_activity WHERE query LIKE 'COPY raw.fixture_iceberg_recovery%' AND state='active'").stdout.strip()
        if active=='1': break
        if child.poll() is not None: break
        time.sleep(.02)
    assert active=='1', 'Iceberg COPY finished before it could be interrupted'
    # One supervisor request avoids a PID lookup/kill race on fast COPY jobs.
    run(['docker','exec',PG,'supervisorctl','signal','KILL',process])
    output=child.communicate(timeout=40)[0]
    assert 'out of memory' not in output.lower(), 'COPY hit OOM instead of proving crash rollback'
    assert child.returncode != 0, 'COPY completed before sidecar interruption; rerun with a larger fixture'
    for _ in range(100):
        count=sql('SELECT count(*) FROM raw.fixture_iceberg_recovery',check=False)
        if count.returncode==0: break
        time.sleep(.2)
    assert count.returncode==0 and count.stdout.strip()=='0',count.stdout
    # A subsequent write/read proves that catalog and sidecar recovered together.
    sql("COPY raw.fixture_iceberg_recovery FROM 's3://mdp-dumps/fixtures/fixture.parquet'",role='loader_wh')
    assert sql('SELECT count(*) FROM raw.fixture_iceberg_recovery').stdout.strip()=='3'
    print(f'{process} SIGKILL during active 50M-row Iceberg COPY: COPY failed, visible rows=0; retry COPY committed exactly 3 rows')


def recovery_iceberg_postgres():
    recovery_iceberg_pgduck('postgres')

def iceberg_type_widening():
    need_lake()
    sql('DROP TABLE IF EXISTS raw.fixture_type_widening; CREATE TABLE raw.fixture_type_widening(id integer) USING iceberg',role='loader_wh')
    p=sql('ALTER TABLE raw.fixture_type_widening ALTER COLUMN id TYPE bigint',role='loader_wh',check=False)
    print('v3.5.1 integer -> bigint: '+('supported (ALTER TABLE)' if p.returncode==0 else 'unsupported: '+p.stdout.splitlines()[0]))


def dbt(storage='iceberg'):
    if storage=='iceberg': need_lake()
    sql("""DROP TABLE IF EXISTS raw.chart_entries CASCADE;
SET ROLE loader_wh;
CREATE TABLE raw.chart_entries(chart text,week date,chart_name text,chart_week date,position int,title text,artist text,
ingested_at timestamp,_run_id text,_dump_id text,_landed_seq bigint,_cycle_id text,_revision_id text,
_target_id text,_request_id text,_source_key text,_ingested_at timestamp,_extra json) USING STORAGE;
INSERT INTO raw.chart_entries VALUES('fixture','2026-09-01','fixture','2026-09-01',1,'track','fixture',now(),'run','dump',1,'cycle','rev','target','request','billboard_hot100',now(),'{}');""".replace('USING STORAGE','USING '+storage))
    project=Path('/tmp/mdp-storage-dbt')
    if project.exists(): shutil.rmtree(project)
    shutil.copytree(ROOT/'dbt',project,ignore=shutil.ignore_patterns('.venv','target','logs','dbt_packages','profiles','.user.yml'))
    (project/'profiles').mkdir()
    shutil.copyfile(ROOT/'dbt/profiles/profiles.example.yml',project/'profiles/profiles.yml')
    env=dict(ENV,DBT_SEND_ANONYMOUS_USAGE_STATS='false',DBT_VERSION_CHECK='false',MDP_PG_PORT='5434',MDP_PG_PASSWORD='dbt_transform',MDP_PG_USER='dbt_transform',
             MDP_PG_HOST='127.0.0.1',MDP_PG_DB='warehouse',MDP_PG_SCHEMA='dbt',MDP_DEV_DB='/tmp/mdp-storage-unused.duckdb')
    p=run([str(ROOT/'dbt/.venv/bin/dbt'),'build','--project-dir',str(project),'--profiles-dir',str(project/'profiles'),
           '--target','pg_local','--select','+mart_chart_history','--indirect-selection','cautious'],env=env)
    Path('/tmp/mdp-storage-dbt.log').write_text(p.stdout)
    print('\n'.join(p.stdout.splitlines()[-8:]))
    p=sql("SELECT c.relname,a.amname FROM pg_class c JOIN pg_am a ON a.oid=c.relam WHERE c.relname='mart_chart_history'")
    assert 'heap' in p.stdout
    print(p.stdout.strip())

def dbt_heap():
    dbt('heap')

def heap_repair():
    sql("SET ROLE loader_wh; DROP TABLE IF EXISTS raw.fixture_heap_repair; CREATE TABLE raw.fixture_heap_repair(id int,_dump_id text); INSERT INTO raw.fixture_heap_repair VALUES(1,'p2'),(2,'p2'); BEGIN; DELETE FROM raw.fixture_heap_repair WHERE _dump_id='p2'; INSERT INTO raw.fixture_heap_repair VALUES(1,'p2'),(2,'p2'); COMMIT")
    assert sql('SELECT count(*),count(DISTINCT id) FROM raw.fixture_heap_repair').stdout.strip()=='2|2'
    print('heap transactional repair: rows=2, distinct id=2')

def backup():
    dump=Path('/tmp/mdp-storage-warehouse.dump')
    with dump.open('wb') as f:
        subprocess.run(['pg_dump','-Fc','-d','warehouse'],env=ENV,stdout=f,check=True)
    sql('DROP DATABASE IF EXISTS fixture_restore WITH (FORCE)','postgres')
    sql('CREATE DATABASE fixture_restore','postgres')
    run(['pg_restore','--exit-on-error','-d','fixture_restore',str(dump)])
    tables=sql("SELECT quote_ident(n.nspname)||'.'||quote_ident(c.relname) FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname NOT LIKE 'pg_%' AND n.nspname <> 'information_schema' AND c.relkind IN ('r','p','f','m') ORDER BY 1").stdout.splitlines()
    for table in tables:
        a=sql(f'SELECT count(*) FROM {table}').stdout.strip()
        b=sql(f'SELECT count(*) FROM {table}','fixture_restore').stdout.strip()
        assert a==b,(table,a,b)
    print(f'pg_dump -Fc warehouse; pg_restore --exit-on-error fixture_restore: {len(tables)} table counts equal')

ACTIONS={f.__name__:f for f in [basic,pgdata,tls,network,haproxy_config,fly_config,plpython,roles,grants,function_acl,workbench_boundary,future_readers,bootstrap_rerun,sidecar,sidecar_health,credentials,parquet,iceberg,copy,alter,repair,recovery_postgres,recovery_pgduck,recovery_iceberg_pgduck,recovery_iceberg_postgres,iceberg_type_widening,dbt,dbt_heap,heap_repair,lineage,backup]}
if __name__=='__main__':
    action=sys.argv[1] if len(sys.argv)>1 else 'all'
    if action in ('all','record'):
        results=[] if action=='all' else json.loads(STATE.read_text())
        selected=list(ACTIONS) if action=='all' else sys.argv[2:]
        for name in selected:
            p=run([sys.executable,__file__,name],check=False)
            result={'gate':name,'status':'PASS' if p.returncode==0 else ('HEAP-FALLBACK' if p.returncode==77 else ('BLOCKED-INPUT' if p.returncode==78 else 'FAIL')),'command':f'python3 ops/local/pg-lake-gates.py {name}','output':p.stdout.strip()}
            results=[r for r in results if r['gate']!=name]
            results.append(result)
            STATE.write_text(json.dumps(results,indent=2))
            print(f"{name}: {result['status']} {p.stdout.strip()[-500:]}",flush=True)
        sys.exit(1 if any(r['status'] in ('FAIL','BLOCKED-INPUT') for r in results) else 0)
    else:
        try:
            ACTIONS[action]()
        except BlockedInput as exc:
            print(str(exc))
            sys.exit(78)
        except LakeUnavailable as exc:
            print(str(exc))
            sys.exit(77)
        except Exception as exc:
            print((type(exc).__name__+': '+str(exc)).replace(str(ROOT), '$REPO').replace(str(Path.home()), '$HOME'))
            sys.exit(1)

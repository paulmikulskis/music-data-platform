"""Initialize the deployed control database via an explicit TLS Fly proxy."""
import csv
import json
import os
import re
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from urllib.parse import quote, urlencode

import psycopg
from mdp_functions.chart_targets import chart_cap, seed_chart_targets
from mdp_functions.explore import install as install_explore
from mdp_functions.exporter import ensure_raw, widened_seeds
from mdp_functions.playlist_targets import seed_playlist_targets
from mdp_functions.promoter import seed_promoter_key
from mdp_functions.rights import sync_rights
from mdp_functions.runbooks import seed_runbooks
from mdp_functions.streamline_defaults import seed_streamline_defaults
from psycopg import sql
from psycopg.conninfo import conninfo_to_dict, make_conninfo

sys.path.insert(0,str(Path(__file__).resolve().parent))
import importlib.util

from showcase_role import reconcile_showcase_role

spec=importlib.util.spec_from_file_location('secret_map',Path(__file__).with_name('secret-map.py'))
secret_map=importlib.util.module_from_spec(spec); spec.loader.exec_module(secret_map)
download=secret_map.download

root=Path(__file__).resolve().parents[2]
d=download()
if d.get('FLY_ORG')!=os.environ.get('FLY_ORG', 'example-org'): raise SystemExit('operator Fly organization required')
host=os.environ.get('MDP_BOOTSTRAP_HOST','127.0.0.1')
port=os.environ.get('MDP_BOOTSTRAP_PORT','15432')
def local_url(key):
    values=conninfo_to_dict(d[key])
    return 'postgresql://'+quote(values['user'],safe='')+':'+quote(values['password'],safe='')+'@'+host+':'+port+'/'+values['dbname']+'?'+urlencode({'sslmode':'require','connect_timeout':'15'})
env=dict(os.environ,MDP_CONTROL_DATABASE_URL=local_url('MDP_CONTROL_DATABASE_URL'))
for attempt in range(30):
    try:
        with psycopg.connect(env['MDP_CONTROL_DATABASE_URL']) as ready:
            ready.execute('SELECT 1')
        break
    except psycopg.OperationalError:
        if attempt==29: raise SystemExit('PostgreSQL readiness failed through private TLS proxy')
        time.sleep(2)
# Existing clusters predate later roles; create a missing one before a migration grants to it.
cluster=make_conninfo(host=host,port=port,dbname='postgres',user='postgres',password=d['POSTGRES_PASSWORD'],sslmode='require')
with psycopg.connect(cluster,autocommit=True) as conn:
    for role,database in (('api_key_reader','control'),):
        if not conn.execute('SELECT 1 FROM pg_roles WHERE rolname=%s',(role,)).fetchone():
            conn.execute(sql.SQL('CREATE ROLE {} LOGIN NOINHERIT PASSWORD {}').format(sql.Identifier(role),sql.Literal(d['MDP_ROLE_PASSWORD_'+role.upper()])))
        conn.execute(sql.SQL('GRANT CONNECT ON DATABASE {} TO {}').format(sql.Identifier(database),sql.Identifier(role)))
    reconcile_showcase_role(conn, root, d['MDP_ROLE_PASSWORD_SHOWCASE_WH'])
migration_context=tempfile.TemporaryDirectory(prefix='mdp-release-migrations-')
subprocess.run(['python3',str(root/'ops/fly/build-context.py'),migration_context.name],check=True,stdout=subprocess.DEVNULL)
env['MDP_MIGRATIONS_DIR']=migration_context.name+'/control/packages/control-db/drizzle'
p=subprocess.run(['node',str(root/'ops/fly/migrate.mjs')],cwd=root/'control',env=env,capture_output=True,text=True,check=False)
migration_context.cleanup()
if p.returncode:
    text=p.stdout+p.stderr
    for value in [*d.values(),env['MDP_CONTROL_DATABASE_URL']]:
        if value and len(value)>20: text=text.replace(value,'<redacted>')
    print(re.sub(r'password=[^\s]+','password=<redacted>',text))
    raise SystemExit('Control migration failed')
with psycopg.connect(env['MDP_CONTROL_DATABASE_URL']) as conn:
    conn.execute((root/'control/packages/control-db/sql/showcase-count.sql').read_text())
# Existing pgdata volumes never execute image first-boot scripts. Reconcile the
# current UDF/grants, and install a successfully built native extension without
# replacing the volume or changing heap storage.
for database in ('control', 'warehouse'):
    admin=make_conninfo(host=host,port=port,dbname=database,user='postgres',password=d['POSTGRES_PASSWORD'],sslmode='require')
    with psycopg.connect(admin) as conn:
        available=conn.execute("SELECT 1 FROM pg_available_extensions WHERE name='pg_lake'").fetchone()
        if available:
            conn.execute('CREATE EXTENSION IF NOT EXISTS pg_lake CASCADE')
            if database=='warehouse':
                conn.execute('GRANT lake_read_write TO loader_wh WITH INHERIT TRUE, SET FALSE')
                conn.execute('GRANT TEMPORARY ON DATABASE warehouse TO loader_wh')
        if database=='warehouse':
            conn.execute((root/'ops/fly/postgres/boot/init/10-warehouse-grants.sql').read_text())
            udf=(root/'ops/fly/postgres/boot/init/20-mdp-udf.sql').read_text()
            # The trailing psql metacommands are replaced with bound parameters.
            conn.execute(udf.split('-- psql reads')[0] + 'COMMIT;')
            for key,env_key in [('service_url','MDP_SERVICE_URL'),('service_token','MDP_SERVICE_TOKEN')]:
                conn.execute('INSERT INTO mdp.config(key,value) VALUES (%s,%s) ON CONFLICT(key) DO UPDATE SET value=EXCLUDED.value',(key,d[env_key]))
print('PASS existing volume UDF/grants reconciled; native extension installed when available')
# Staging reads raw tables that on Fly only a landing would create, and bootstrap_raw is a
# dev/ci shim, so create every declared raw table and column now (additive, as loader_wh).
with psycopg.connect(make_conninfo(cluster, dbname='warehouse')) as conn:
    for filename in ('15-label-catalog.sql', '16-label-definitions.sql','17-explore-views.sql','18-query-statistics.sql'):
        conn.execute((root / 'ops/fly/postgres/boot/init' / filename).read_text())
created=ensure_raw(local_url('MDP_WAREHOUSE_URL'),root/'functions/schemas')
with psycopg.connect(make_conninfo(cluster, dbname='warehouse')) as conn:
    install_explore(conn)
print('PASS declared raw tables: '+('; '.join(created) if created else 'already present'))
with psycopg.connect(local_url('MDP_CONTROL_DATABASE_URL')) as conn:
    conn.execute("INSERT INTO control.warehouse(adapter,database,dsn_secret_ref,is_production) SELECT 'postgres','warehouse','MDP_WAREHOUSE_URL',true WHERE NOT EXISTS(SELECT 1 FROM control.warehouse WHERE is_production)")
with psycopg.connect(local_url('MDP_CONTROL_RT_URL')) as conn:
    conn.execute("INSERT INTO control.runner_mode(id,runner) VALUES(true,'core') ON CONFLICT(id) DO NOTHING")
    # deploy.sh creates one core-runner machine per (cadence, active tenant) from this list.
    if os.environ.get('MDP_CORE_TENANTS_FILE'):
        tenants=[{'id':str(i),'slug':s} for i,s in conn.execute("SELECT id,slug FROM control.tenant WHERE status='active' ORDER BY slug")]
        Path(os.environ['MDP_CORE_TENANTS_FILE']).write_text(json.dumps(tenants))
    seed_runbooks(conn)
    from mdp_functions.expected_schema import acknowledge
    try:
        with conn.transaction():
            print(f"PASS expected fixture schemas {acknowledge(conn, root / 'functions/schemas')}")
    except Exception as exc:  # noqa: BLE001 - advisory checks fail open
        print(f"WARN expected schema check unavailable ({type(exc).__name__}); bootstrap continues")
    # The promoters' control-API key, which secret-map.py provision minted in secret store.
    if not d.get('MDP_CONTROL_API_KEY'): raise SystemExit('secret store prd MDP_CONTROL_API_KEY is missing; run ops/fly/secret-map.py provision')
    seed_promoter_key(conn, d['MDP_CONTROL_API_KEY'])
    # Include an empty global track set before restore or a daily export freezes membership.
    seed_playlist_targets(conn, root / "inputs/playlist_seed.csv", activate=False)
    seed_chart_targets(conn, root / "inputs/chart_seed.csv", chart_cap(path=root / "dbt/seeds/chart_caps.csv"), activate=False)
# The rights registry's control mirror, as rights_sync, before the new functions image lands a row: a gold
# row's learning flag reads control.rights_source.
rights=make_conninfo(host=host,port=port,dbname='control',user='rights_sync',password=d['MDP_ROLE_PASSWORD_RIGHTS_SYNC'],sslmode='require')
with psycopg.connect(rights) as conn:
    print(f'PASS rights registry mirrored to control.rights_source: {sync_rights(conn, root/"dbt/seeds/rights_registry.csv")} rows')
# Before the new functions image syncs: new streamlines start on their declared knobs
# (gated collectors disabled), once; the retired Spotify surfaces stop.
with psycopg.connect(local_url('MDP_CONTROL_URL')) as functions_conn, psycopg.connect(local_url('MDP_CONTROL_RT_URL')) as control_conn:
    applied=seed_streamline_defaults(functions_conn, control_conn)
print('PASS streamline defaults: '+(', '.join(applied) if applied else 'already recorded'))
# Seed only the committed runtime's reference data, without opening a cycle.
with tempfile.TemporaryDirectory(prefix='mdp-release-seed-') as context:
    subprocess.run(['python3',str(root/'ops/fly/build-context.py'),context],check=True,stdout=subprocess.DEVNULL)
    profiles=Path(context)/'dbt/profiles'
    (profiles/'profiles.yml').write_text((profiles/'profiles.example.yml').read_text())
    seed_env=dict(os.environ,**d,MDP_DEV_DB=context+'/dev.duckdb',UV_NO_DEV='1')
    seed_env.update(MDP_PG_HOST=host,MDP_PG_PORT=port)
    command=['uv','run','--project',str(root/'dbt'),'dbt','seed','--project-dir',context+'/dbt','--profiles-dir',str(profiles),'--target','pg','--vars','{"dry_run":true}']
    # A seed whose CSV gained a column is recreated first: a plain dbt seed truncates the old table and
    # inserts into its columns, which fails on the new one. Every other seed keeps its table.
    admin=make_conninfo(host=host,port=port,dbname='warehouse',user='postgres',password=d['POSTGRES_PASSWORD'],sslmode='require')
    with psycopg.connect(admin) as conn: widened=widened_seeds(conn,root/'dbt/seeds')
    result=subprocess.run(command+['--full-refresh','--select',*widened],cwd=root,env=seed_env,capture_output=True,text=True,check=False) if widened else None
    if widened: print('PASS widened seeds recreated: '+', '.join(widened) if not result.returncode else 'FAIL widened seeds')
    if result is None or not result.returncode:
        result=subprocess.run(command,cwd=root,env=seed_env,capture_output=True,text=True,check=False)
    if result.returncode:
        text=result.stdout+result.stderr
        for value in d.values():
            if value and len(value)>20: text=text.replace(value,'<redacted>')
        print(re.sub(r'password=[^\s]+','password=<redacted>',text))
        raise SystemExit('Warehouse reference seed failed')
print('PASS control migrations, runner_mode=core, warehouse registration, declared raw tables, runbooks, promoter key, targets, rights mirror, streamline defaults, committed reference seeds')

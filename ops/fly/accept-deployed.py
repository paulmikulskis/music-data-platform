"""Deployed acceptance; real Fly processes and database observations only."""
import argparse
from datetime import datetime, timezone
import importlib.util
import json
import os
from pathlib import Path
import re
import subprocess
import time
from uuid import uuid4
import psycopg
from psycopg.conninfo import conninfo_to_dict, make_conninfo
from mdp_functions.warehouse.postgres import manifest_sql

ROOT=Path(__file__).resolve().parents[2]
parser=argparse.ArgumentParser()
parser.add_argument('--deployed',action='store_true',required=True)
parser.add_argument('--lifecycle-only',action='store_true')
args=parser.parse_args()
spec=importlib.util.spec_from_file_location('secret_map',Path(__file__).with_name('secret-map.py'))
secrets=importlib.util.module_from_spec(spec); spec.loader.exec_module(secrets)
d=secrets.download()
assert d.get('FLY_ORG')==os.environ.get('FLY_ORG', 'example-org')
path=ROOT/'ops/evidence/platform'/('accept-platform-deployed-'+datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')+'.txt')
case_evidence=path.parent/('deployed-lifecycle-'+path.stem.removeprefix('accept-platform-deployed-'))
log=path.open('w')
def clean(text):
    for key,value in sorted(d.items(),key=lambda kv:len(kv[1]),reverse=True):
        if value and any(x in key for x in ('PASSWORD','TOKEN','SECRET','KEY','_URL')): text=text.replace(value,'<redacted>')
    text=re.sub(r'[\w.+-]+@[\w.-]+\.[A-Za-z]+','<identity>',text)
    text=re.sub(r'./\s]+','<home>',text)
    text=re.sub(r'postgres(?:ql)?://[^\s]+','<database-url>',text)
    text=re.sub(r'password=[^\s]+','password=<redacted>',text)
    text=re.sub(r'\$\d+(?:\.\d+)?(?:/\w+)?','<redacted>',text)
    return text

def note(text):
    text=clean(str(text)); log.write(text+'\n'); log.flush(); print(text,flush=True)
def run(cmd,env=None,ok=True,timeout=900,quiet=False):
    note('COMMAND '+' '.join(cmd))
    p=subprocess.run(cmd,cwd=ROOT,env=env or dict(os.environ,**d),capture_output=True,text=True,timeout=timeout)
    if not quiet: note(p.stdout+p.stderr)
    if ok and p.returncode: raise RuntimeError('Command failed: '+cmd[0])
    return p

def fly(*cmd,app='mdp-core-runner'):
    p=run(['bash','ops/fly/fly.sh',*cmd,'--org',os.environ.get('FLY_ORG', 'example-org'),'--app',app],quiet='--json' in cmd)
    return p.stdout if '--json' in cmd else p.stdout+p.stderr

def frontend(key):
    params=conninfo_to_dict(d[key]); params.update(host='mdp-pg-frontend.example.invalid',port='5432',sslmode='require')
    return make_conninfo(**params)

def scalar(conn,sql,params=()): return conn.execute(sql,params).fetchone()[0]

test_machines=set()
def machine(cadence,reason='scheduled'):
    run_id='acceptance:'+str(uuid4()); name='release-'+uuid4().hex[:12]
    fly('machine','run','registry.fly.io/mdp-core-runner:p5',cadence,'--name',name,'--region','ewr','--restart','no','--vm-size','shared-cpu-2x','--vm-memory','2048','--env','MDP_RUN_ID='+run_id,'--env','MDP_RUN_REASON_CATEGORY='+reason,'--detach')
    deadline=time.monotonic()+7500; identity=None
    while time.monotonic()<deadline:
        listing=json.loads(fly('machine','list','--json'))
        found=next((m for m in listing if m['name']==name),None)
        if found:
            identity=found['id']; test_machines.add(identity)
            if found['state']=='stopped':
                record=found
                exits=[e for e in record.get('events',[]) if e.get('type')=='exit']
                code=next((e['request']['exit_event'].get('exit_code',0) for e in exits if 'exit_event' in e.get('request',{})),None)
                logs=fly('logs','--instance',identity,'--no-tail')
                if code is None:
                    observed=re.findall(r'Main child exited (?:normally )?with code: (\d+)',logs)
                    if observed: code=int(observed[-1])
                    note('Exit evidence: Fly init process log (machine-list events omit exit details)')
                note(f'MACHINE cadence={cadence} id={identity} exit={code}')
                return run_id,code,logs,identity
        time.sleep(10)
    raise RuntimeError('Core machine did not complete within acceptance deadline')

paused=[]
def pause_schedules():
    listing=json.loads(fly('machine','list','--json'))
    schedules={m.get('config',{}).get('schedule') for m in listing if m.get('config',{}).get('schedule')}
    if schedules != {'hourly','daily','weekly'}: raise RuntimeError('Expected all three deployed Core schedules')
    for row in listing:
        schedule=row.get('config',{}).get('schedule')
        if not schedule: continue
        deadline=time.monotonic()+7500
        while row['state']!='stopped':
            if time.monotonic()>deadline: raise RuntimeError('Scheduled Core run did not finish before acceptance')
            time.sleep(10)
            row=next(m for m in json.loads(fly('machine','list','--json')) if m['id']==row['id'])
        fly('machine','update',row['id'],'--machine-config','{"schedule":""}','--skip-start','--yes')
        paused.append((row['id'],schedule))
        updated=next(m for m in json.loads(fly('machine','list','--json')) if m['id']==row['id'])
        if updated.get('config',{}).get('schedule'): raise RuntimeError('Fly CLI did not clear the Core schedule; acceptance cannot overlap scheduled runs')
    note('PASS scheduled Core machines quiesced for acceptance')

try:
    preflight=run(['bash','ops/preflight.sh','--stage','0','--json'],ok=False)
    rows=json.loads(preflight.stdout)
    # Interim addendum explicitly permits these four missing inputs.
    allowed={'r2','dbtcloud','lifecycle_daily_probe','fixture_targets'}
    required={'fly.token','fly.org','fly.region','secret_store','billboard','budgets','github'} | allowed
    required.update(r['name'] for r in rows if r['name'].startswith('local.'))
    failures=[r['name'] for r in rows if r['name'] in required and r['status']!='PASS' and not (r['name'] in allowed and r['status']=='MISSING')]
    if failures: raise RuntimeError('Preflight failed: '+','.join(failures))
    for row in rows:
        if row['name'] in allowed and row['status']!='PASS': note('INTERIM '+row['name']+' '+row['status'])
    env=dict(os.environ,**d)
    for key in ['MDP_CONTROL_URL','MDP_CONTROL_RT_URL','MDP_WAREHOUSE_URL','MDP_SERVICE_READ_URL']:
        env[key]=frontend(key)
    env['MDP_PG_HOST']='mdp-pg-frontend.example.invalid'; env['MDP_PG_PORT']='5432'
    env['MDP_LIFECYCLE_ALLOW_REMOTE']='1'
    env['MDP_SERVICE_URL']=os.environ.get('MDP_ACCEPT_SERVICE_URL','http://127.0.0.1:18080')
    env['MDP_CONTROL_ADMIN_URL']=make_conninfo(host='mdp-pg-frontend.example.invalid',port=5432,dbname='control',user='postgres',password=d['POSTGRES_PASSWORD'],sslmode='require')
    api_machines=[m for m in json.loads(fly('machine','list','--json',app='mdp-functions'))
                  if m.get('config',{}).get('metadata',{}).get('fly_process_group')=='api']
    if len(api_machines)!=1: raise RuntimeError('Acceptance requires one volume-backed API machine')
    api_id=api_machines[0]['id']
    env['MDP_SERVICE_RESTART_COMMAND']=f'bash ops/fly/fly.sh machine restart {api_id} --org example-org --app mdp-functions'
    env['MDP_SERVICE_STOP_COMMAND']=f'bash ops/fly/fly.sh machine stop {api_id} --org example-org --app mdp-functions'
    with psycopg.connect(env['MDP_CONTROL_RT_URL'],autocommit=True) as control, psycopg.connect(env['MDP_SERVICE_READ_URL'],autocommit=True) as warehouse:
        assert scalar(warehouse,'SELECT ssl FROM pg_stat_ssl WHERE pid=pg_backend_pid()')
        pause_schedules()
        if not args.lifecycle_only:
            for cadence in ('weekly',):
                run_id,code,logs,identity=machine(cadence)
                if code!=0: raise RuntimeError('Core '+cadence+' failed')
                cycle=control.execute('SELECT c.id,c.status FROM control.cycle c JOIN control.cycle_attempt b ON b.cycle_id=c.id WHERE b.dbt_run_id=%s',(run_id,)).fetchone()
                if not cycle or cycle[1]!='closed': raise RuntimeError('Cycle missing or not closed')
                note(f'PASS {cadence} scheduled Core cycle={cycle[0]} closed')
                table='account_snapshots' if cadence=='hourly' else 'chart_entries'
                # Catalog assertions below are deliberately strict: no rows means no acceptance.
                count=scalar(warehouse,f'SELECT count(*) FROM raw.{table} WHERE _cycle_id=%s',(cycle[0],))
                if count<=0: raise RuntimeError('No landed lineage rows')
                required=['_run_id','_dump_id','_landed_seq','_cycle_id','_request_id','_source_key','_ingested_at','_extra']
                if cadence=='hourly': required+=['_revision_id','_target_id']
                nulls=scalar(warehouse,f'SELECT count(*) FROM raw.{table} WHERE _cycle_id=%s AND ('+' OR '.join(c+' IS NULL' for c in required)+')',(cycle[0],))
                if nulls: raise RuntimeError('Missing raw lineage')
                manifest={str(r[0]) for r in control.execute('SELECT dump_id FROM control.cycle_manifest(%s)',(cycle[0],))}
                mirror={str(r[0]) for r in warehouse.execute(manifest_sql('%(cycle)s'),{'cycle':cycle[0]})}
                landed={str(r[0]) for r in warehouse.execute(f'SELECT DISTINCT _dump_id FROM raw.{table} WHERE _cycle_id=%s',(cycle[0],))}
                if not manifest or manifest!=mirror or not landed<=manifest: raise RuntimeError('Closed manifest mismatch')
                staging='stg_billboard__chart_entries' if cadence=='hourly' else 'stg_billboard__chart_entries'
                staged={str(r[0]) for r in warehouse.execute(f'SELECT DISTINCT _dump_id FROM staging.{staging}')}
                if not staged or not staged<=manifest: raise RuntimeError('Staging does not match manifest')
                mart='mart_chart_history'
                if scalar(warehouse,f'SELECT count(*) FROM marts.{mart}')<=0: raise RuntimeError('Mart empty')
                if scalar(warehouse,f'SELECT count(*) FROM marts.{mart} WHERE _cycle_id::text<>%s',(str(cycle[0]),)):
                    raise RuntimeError('Mart was built against another cycle')
                note(f'PASS raw.{table} cycle rows={count}; lineage complete; manifest mirrored; staging and {mart} built')
                fly('machine','destroy',identity,'--force')
        # Probe inactive Core even while Cloud itself is not configured.
        control.execute("UPDATE control.runner_mode SET runner='cloud' WHERE id=true")
        try:
            _,code,logs,identity=machine('hourly')
            if code is None or code==0 or 'runner_inactive' not in logs:
                raise RuntimeError('Inactive Core refusal unproven')
            note('PASS Core exits nonzero with runner_inactive while runner_mode=cloud')
            fly('machine','destroy',identity,'--force')
        finally:
            control.execute("UPDATE control.runner_mode SET runner='core' WHERE id=true")
        # The harness changes only its owned fixture cycles; scheduled clocks are paused.
        if os.environ.get('MDP_LIFECYCLE_CLAIM_STACK')=='1':
            # The operator's explicit first-deployment claim never overrides an
            # existing production designation or a different stack fingerprint.
            production=scalar(control,"SELECT count(*) FROM control.audit_log WHERE action IN ('production_stack_marked','stack_marked_production','lifecycle_stack_production')")
            if production or d.get('MDP_STACK_PRODUCTION','').lower() in ('1','true','yes'):
                raise RuntimeError('Operator marked this stack production; disposable claim refused')
            note('First-deployment disposable claim authorized; remove lifecycle_stack_claimed before real client data')
            claim=run(['bash','ops/ci/lifecycle.sh','--target','pg','--reset','--claim','--evidence-dir',str(case_evidence.relative_to(ROOT))],env=env,timeout=300,ok=False)
            if claim.returncode:
                note('Claim refused by lifecycle safety; full suite will report each guarded case')
        run(['bash','ops/ci/lifecycle.sh','--target','pg','--case','all','--evidence-dir',str(case_evidence.relative_to(ROOT))],env=env,timeout=7200)
        note('PASS lifecycle --target pg --case all through frontend TLS')
        if all(d.get(k) for k in ['DBT_CLOUD_TOKEN','DBT_CLOUD_ACCOUNT_ID','DBT_CLOUD_PROJECT_ID','DBT_CLOUD_ENV_ID_PROD','DBT_CLOUD_ENV_ID_CI']):
            control.execute("UPDATE control.runner_mode SET runner='cloud' WHERE id=true")
            try:
                _,code,logs,identity=machine('hourly')
                if code==0 or 'runner_inactive' not in logs: raise RuntimeError('Inactive Core refusal unproven')
                note('PASS runner selection: Core exits nonzero with runner_inactive in cloud mode')
                fly('machine','destroy',identity,'--force')
            finally: control.execute("UPDATE control.runner_mode SET runner='core' WHERE id=true")
        else: note('PHASE_BLOCKED step=dbtcloud reason=DBT_CLOUD_*; see ops/fly/SECRETS.md')
    note('ACCEPT platform deployed PASS (interim Core clock, live Billboard, fixture lifecycle probe)')
except Exception as error:
    note('ACCEPT platform deployed FAIL '+(str(error) if isinstance(error,(RuntimeError,AssertionError)) else type(error).__name__))
    raise SystemExit(1) from None
finally:
    cleanup_failed=False
    for identity in test_machines:
        try:
            # Successful probes may already have removed their machine.
            if any(m['id']==identity for m in json.loads(fly('machine','list','--json'))):
                fly('machine','destroy',identity,'--force')
        except Exception:
            cleanup_failed=True; note('FAIL temporary machine cleanup '+identity)
    for identity,schedule in paused:
        try: fly('machine','update',identity,'--schedule',schedule,'--skip-start','--yes')
        except Exception:
            cleanup_failed=True; note('FAIL restoring schedule '+identity)
    log.close(); print(path.relative_to(ROOT))
    if cleanup_failed: raise SystemExit(1)

from datetime import date
import json
import os
from pathlib import Path
import subprocess
import tempfile
root=Path(os.environ.get('MDP_MEASURE_ROOT',Path(__file__).resolve().parents[2]))
with tempfile.TemporaryDirectory(prefix='mdp-selectors-') as temp:
    totals={}
    for cadence in ['hourly','daily','weekly']:
        materialized=set()
        for phase in ['bronze','transform']:
            selector=f'{cadence}_global_{phase}'
            command=['uv','run','--project','dbt','dbt','ls','--project-dir','dbt','--profiles-dir','dbt/profiles','--target','ci','--selector',selector,'--resource-type','model','--output','json','--output-keys','name','config','--quiet','--target-path',temp+'/target','--log-path',temp+'/logs']
            p=subprocess.run(command,cwd=root,capture_output=True,text=True,env=dict(os.environ,MDP_PG_PASSWORD='unused',MDP_DEV_DB=temp+'/dev.duckdb'))
            if p.returncode: raise SystemExit('dbt ls failed: '+p.stdout+p.stderr)
            models=[json.loads(line) for line in p.stdout.splitlines() if line.startswith('{')]
            materialized.update(m['name'] for m in models if m['config']['materialized']!='ephemeral')
            print(f'PASS {date.today()} dbt ls --selector {selector}: models={len(models)}')
        totals[cadence]=len(materialized)
    print('MATERIALIZED '+json.dumps(totals))
    print('MONTHLY 720*H + 30*D + 4*W + P*C; add ten percent headroom')

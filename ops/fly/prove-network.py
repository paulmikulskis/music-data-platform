"""Positive frontend TLS and negative plaintext probes, with credentials in memory."""
import os
import importlib.util
from pathlib import Path
import psycopg
from psycopg.conninfo import conninfo_to_dict,make_conninfo
spec=importlib.util.spec_from_file_location('secret_map',Path(__file__).with_name('secret-map.py'))
m=importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
d=m.download(); params=conninfo_to_dict(d['MDP_CONTROL_RT_URL'])
params.update(host='mdp-pg-frontend.example.invalid',port='5432',sslmode='require',connect_timeout='10')
with psycopg.connect(make_conninfo(**params)) as conn:
    assert conn.execute('SELECT ssl FROM pg_stat_ssl WHERE pid=pg_backend_pid()').fetchone()[0]
    assert conn.execute('SELECT current_user').fetchone()[0]=='control_rt'
print('PASS allowlisted operator connects through mdp-pg-frontend.example.invalid:5432 with TLS as control_rt')
params['sslmode']='disable'
try:
    with psycopg.connect(make_conninfo(**params)): pass
except psycopg.OperationalError:
    print('PASS plaintext frontend connection rejected')
else:
    raise SystemExit('FAIL plaintext connection accepted')
import subprocess
import shlex
probe='import socket,struct; s=socket.create_connection(("mdp-pg-frontend.example.invalid",5432),10); s.sendall(struct.pack("!II",8,80877103)); s.settimeout(10); r=s.recv(1); assert r!=b"S", "unexpected TLS admission"; print("PASS non-allowlisted Fly egress rejected by frontend")'
result=subprocess.run(['bash','ops/fly/fly.sh','ssh','console','--command','python3 -c '+shlex.quote(probe),'--org',os.environ.get('FLY_ORG', 'example-org'),'--app','mdp-postgres'],capture_output=True,text=True,timeout=30)
if result.returncode: raise SystemExit('Non-allowlisted connection probe failed: '+result.stderr)
print(result.stdout.strip())

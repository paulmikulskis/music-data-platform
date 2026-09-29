"""Runtime startup proof for the private API containers; no real database access."""
import json
import subprocess
import time
import urllib.request
from uuid import uuid4
for app,port in [('control-api',18990),('data-api',18991)]:
 name='mdp-release-proof-'+uuid4().hex[:10]
 cmd=['docker','run','-d','--rm','--platform','linux/amd64','--name',name,'-p',f'127.0.0.1:{port}:8080','-e','HOST=0.0.0.0','-e','PORT=8080','-e','NODE_ENV=production','-e','MDP_CONTROL_RT_URL=postgresql://unused@127.0.0.1:1/control','-e','MDP_READER_URL=postgresql://unused@127.0.0.1:1/warehouse','mdp-release-'+app+':proof']
 subprocess.run(cmd,check=True,stdout=subprocess.DEVNULL)
 try:
  for _ in range(60):
   try:
    with urllib.request.urlopen(f'http://127.0.0.1:{port}/health',timeout=2) as r:
     assert json.load(r)['status']=='ok'
    print('PASS '+app+' built image serves /health in production auth mode',flush=True)
    break
   except (OSError,ValueError): time.sleep(1)
  else: raise RuntimeError(app+' startup failed')
 finally: subprocess.run(['docker','stop',name],stdout=subprocess.DEVNULL,check=True)

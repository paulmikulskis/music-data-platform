"""Check the exact built deployment's mixed transport and Alloy configuration."""
import subprocess
from pathlib import Path

root=Path(__file__).resolve().parents[2]
checks=[
 ['docker','run','--rm','--platform','linux/amd64','--entrypoint','/bin/alloy','-e','OTLP_ENDPOINT=https://example.invalid/otlp','-e','OTLP_AUTH_HEADER=placeholder','mdp-release-alloy:proof','validate','/etc/alloy/config.alloy'],
 ['docker','run','--rm','--platform','linux/amd64','--entrypoint','python','mdp-release-functions:proof','-c', '''import sys,asyncio,httpx
sys.path.insert(0,"/opt/mdp")
from serve import InterimTransport,PLANS,FixturePlan
class DB:
 def one(self,*args): return {"index":0,"platform_account_id":"acceptance-001"}
async def main():
 PLANS["lifecycle_daily_probe"]=FixturePlan(source_key="lifecycle_daily_probe")
 t=InterimTransport(DB())
 r=await t.handle_async_request(httpx.Request("GET","https://fixture.invalid/lifecycle_daily_probe?username=acceptance_account_001"))
 assert r.status_code==200
 r=await t.handle_async_request(httpx.Request("GET","https://www.billboard.com/charts/hot-100/"))
 assert r.status_code==200 and len(r.content)>10000
 print("PASS fixture lifecycle probe and live Billboard routes in built deployment image")
asyncio.run(main())'''],
]
for cmd in checks:
 p=subprocess.run(cmd,cwd=root,capture_output=True,text=True,timeout=180,check=False)
 if p.returncode: raise SystemExit('Runtime check failed: '+p.stdout+p.stderr)
 print(p.stdout.strip() or 'PASS Alloy validates')

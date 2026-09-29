#!/usr/bin/env python3
"""Verify acceptance failure propagation; run only when no gate run is active."""
import importlib.util
import json
from pathlib import Path
import subprocess
import sys

script=Path(__file__).with_name('pg-lake-gates.py').resolve()
spec=importlib.util.spec_from_file_location('gates',script)
gates=importlib.util.module_from_spec(spec)
spec.loader.exec_module(gates)
gates.lake=lambda: False
for mode in ('0','1'):
    gates.run=lambda *args, **kwargs: subprocess.CompletedProcess([],0,mode+'\n')
    try:
        gates.need_lake()
        raise AssertionError('missing extension accepted')
    except gates.LakeUnavailable:
        assert mode=='0'
    except RuntimeError:
        assert mode=='1'
state=gates.STATE
saved=state.read_bytes() if state.exists() else None
try:
    for status,expected in (('FAIL',1),('BLOCKED-INPUT',1),('HEAP-FALLBACK',0)):
        state.write_text(json.dumps([{'gate':'controlled_fixture','status':status}]))
        result=subprocess.run([sys.executable,str(script),'record'],capture_output=True,text=True)
        assert result.returncode==expected,(status,result.returncode)
finally:
    if saved is None: state.unlink(missing_ok=True)
    else: state.write_bytes(saved)
print('PASS required failure/blocked input exit=1; explicit heap fallback exit=0; missing pg_lake in enabled image is a failure')

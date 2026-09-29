#!/usr/bin/env python3
"""Run the control proof with the wb_* schema contract, without editing control/."""
from pathlib import Path
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[2]
PACKAGE = ROOT / 'control/packages/control-db'
source = (PACKAGE / 'scripts/prove-grants.ts').read_text()
old = "await check(warehouse, role, 'warehouse CREATE SCHEMA', 'CREATE SCHEMA proof_forbidden', role === 'dbt_transform' || role === 'workbench_wh' ? 'success' : '42501');"
new = """await check(warehouse, role, 'warehouse CREATE SCHEMA wb_*', 'CREATE SCHEMA wb_proof_allowed', role === 'dbt_transform' || role === 'workbench_wh' ? 'success' : '42501');
    if (role === 'workbench_wh') await check(warehouse, role, 'warehouse CREATE SCHEMA outside wb_*', 'CREATE SCHEMA proof_forbidden', '42501');"""
assert source.count(old) == 1, 'Upstream proof changed: review the sandbox adaptation'
source = source.replace(old, new)
# Retain every original check except the schema name; add the negative check.
source = source.replace("new URL('../../../../ops/evidence/platform/', import.meta.url)", repr((ROOT / 'ops/evidence/platform').as_uri() + '/'))
with tempfile.TemporaryDirectory(prefix='mdp-grants-proof-') as directory:
    package = Path(directory)
    for child in PACKAGE.iterdir():
        if child.name != 'scripts':
            (package / child.name).symlink_to(child, target_is_directory=child.is_dir())
    (package / 'scripts').mkdir()
    script = package / 'scripts/prove-grants.ts'
    script.write_text(source)
    result = subprocess.run([str(PACKAGE / 'node_modules/.bin/tsx'), str(script)], cwd=PACKAGE)
    raise SystemExit(result.returncode)

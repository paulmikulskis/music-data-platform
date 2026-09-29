"""Shared fixtures for cycle input mirrors tests."""

import json
import os
import subprocess

from conftest import url_database
from psycopg.conninfo import conninfo_to_dict


def control_rt(databases):
    return url_database(
        os.environ.get("MDP_CONTROL_RT_URL") or os.environ["MDP_CONTROL_RT_DATABASE_URL"],
        conninfo_to_dict(databases["control_url"])["dbname"],
    )


def render(macro, *args):
    """An mdp_context.sql macro rendered with dbt's Jinja: `return` captures the value, the generated
    mdp_global_inputs()/mdp_global_tables() answer as dbt would, and a compiler error fails the render."""
    program = f"""
import json, re
from pathlib import Path
from jinja2 import Environment
captured = []
generated = Path('dbt/macros/mdp_global_inputs.sql').read_text()
declared = json.loads(re.search(r"set declared = (\\{{.*?\\}}) %\\}}", generated).group(1))
tables = json.loads(re.search(r"return\\((\\[.*?\\])\\)", generated.split('macro mdp_global_tables', 1)[1]).group(1))
class Exceptions:
    @staticmethod
    def raise_compiler_error(message):
        raise SystemExit(message)
environment = Environment(extensions=['jinja2.ext.do'])
environment.globals.update({{
    'return': lambda value: captured.append(value) or '', 'fromjson': json.loads, 'exceptions': Exceptions,
    'mdp_global_inputs': lambda cadence: declared.get(cadence, []), 'mdp_global_tables': lambda: tables,
}})
getattr(environment.from_string(Path('dbt/macros/mdp_context.sql').read_text()).module, {macro!r})(*{list(args)!r})
print(json.dumps(captured[-1]))
"""
    result = subprocess.run(
        ["uv", "run", "--project", "dbt", "python", "-c", program], capture_output=True, text=True, check=False
    )
    return json.loads(result.stdout) if result.returncode == 0 else None

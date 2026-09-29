#!/usr/bin/env python3
"""Render supplied storage measurements and build logs without inferred results.

Run gates first, then pass a JSON manifest containing outcome, rows, attempts and
review: python3 ops/local/write-pg-lake-evidence.py /tmp/mdp-storage-evidence.json
Gate results default to /tmp/mdp-storage-gate-results.json. A second argument can
select another results file. Evidence is written under ignored ops/evidence/.
"""
from datetime import datetime, timezone
import json
from pathlib import Path
import re
import sys

ROOT = Path(__file__).resolve().parents[2]


def clean(value):
    return re.sub(r'\x1b\[[0-9;]*[A-Za-z]', '', str(value)).replace(
        str(ROOT), '$REPO'
    ).replace(str(Path.home()), '$HOME').strip()


def cell(value):
    return clean(value).replace('|', '\\|').replace('\n', '<br>')


def render(manifest, results):
    text = '# Postgres storage gates\n\nMeasured ' + datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC') + '.\n\n'
    text += clean(manifest['outcome']) + '\n\n'
    text += '| Gate | Status | Exact command | Observed output |\n|---|---|---|---|\n'
    rows = list(manifest.get('rows', []))
    for result in results:
        lines = clean(result['output']).splitlines()
        rows.append([result['gate'], result['status'], result['command'], lines[-1] if lines else 'No output'])
    for row in rows:
        text += '| ' + ' | '.join(cell(value) for value in row) + ' |\n'
    text += '\n## Build attempts\n\n'
    for attempt in manifest.get('attempts', []):
        text += clean(attempt['description']) + '\n\n`' + clean(attempt['command']) + '`\n\n'
        text += '```text\n' + clean('\n'.join(Path(attempt['log']).read_text().splitlines()[-20:])) + '\n```\n\n'
    text += '## Review\n\n' + clean(manifest.get('review', 'No additional review supplied.')) + '\n\n'
    for result in results:
        text += '### ' + clean(result['gate']) + '\n\n`' + clean(result['command']) + '` — ' + clean(result['status']) + '\n\n'
        text += '```text\n' + clean(result['output']) + '\n```\n\n'
    text += 'Only the supplied measurements are represented here; local checks do not establish deployed behavior.\n\n'
    text += 'Sources: [PostgreSQL event triggers](https://www.postgresql.org/docs/17/event-trigger-definition.html), [default privileges](https://www.postgresql.org/docs/17/sql-alterdefaultprivileges.html), [pg_lake build recipe](https://github.com/Snowflake-Labs/pg_lake/blob/v3.5.1/docs/building-from-source.md).\n'
    return text


if __name__ == '__main__':
    manifest = json.loads(Path(sys.argv[1]).read_text())
    results = json.loads(Path(sys.argv[2] if len(sys.argv) > 2 else '/tmp/mdp-storage-gate-results.json').read_text())
    output = ROOT / 'ops/evidence/platform/pg-lake-gates.md'
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(render(manifest, results))
    print('Wrote ops/evidence/platform/pg-lake-gates.md')

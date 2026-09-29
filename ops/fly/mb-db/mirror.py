"""psql against the MusicBrainz mirror.

Production runs each psql script on mdp-mb-db through the guarded `fly.py ssh console` path, as the
musicbrainz superuser over the local socket; the script travels on stdin, so no password or private
URL appears in a command line. Tests run the same scripts with a local psql. Scripts print the rows
they return as `MDP_JSON {...}` lines; everything else in the output is ignored.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
IMPORT_DIR = ROOT / 'ops/fly/mb-import'
ORG = os.environ.get('FLY_ORG', '')
DB_APP = os.environ.get('FLY_APP_MB_DB', '')
# The mirror's only machine; override when it is replaced.
DB_MACHINE = os.environ.get('MDP_MB_DB_MACHINE', '')
DATA_PATH = '/var/lib/postgresql'

IDENT = re.compile(r'^[a-z_][a-z0-9_]{0,62}$')
STAMP = re.compile(r'^[0-9]{8}-[0-9]{6}$')
INCLUDE = re.compile(r'^\\ir\s+(\S+)\s*$')


class MirrorError(RuntimeError):
    pass


@dataclass(frozen=True)
class Names:
    """Database names on the mirror server. Readers always use `serving`."""
    serving: str = 'musicbrainz_db'
    next: str = 'musicbrainz_next'
    prev: str = 'musicbrainz_prev'
    meta: str = 'mdp_meta'

    def __post_init__(self) -> None:
        names = [self.serving, self.next, self.prev, self.meta]
        if len(set(names)) != 4 or not all(IDENT.match(n) for n in names):
            raise ValueError(f'invalid database names: {names}')


def bundle(path: Path) -> str:
    """Inline `\\ir` includes, because psql reading stdin resolves them against its own cwd."""
    lines = []
    for line in path.read_text().splitlines():
        match = INCLUDE.match(line)
        lines.append(bundle((path.parent / match.group(1)).resolve()) if match else line)
    return '\n'.join(lines) + '\n'


def prelude(**variables: str | int) -> str:
    """psql \\set lines for validated values only: identifiers, export stamps and integers."""
    out = []
    for name, value in variables.items():
        text = str(value)
        if not (isinstance(value, int) or IDENT.match(text) or STAMP.match(text)):
            raise ValueError(f'unsafe psql variable {name}={text!r}')
        out.append(f'\\set {name} {text}')
    return '\n'.join(out) + '\n'


def records(output: str) -> dict[str, Any]:
    """Merge every `MDP_JSON {...}` line of a script's output."""
    merged: dict[str, Any] = {}
    for line in output.splitlines():
        if line.startswith('MDP_JSON '):
            merged.update(json.loads(line[len('MDP_JSON '):]))
    return merged


class Sql(Protocol):
    def psql(self, db: str, script: str) -> str: ...
    def volume_total(self) -> int: ...


def _check(result: subprocess.CompletedProcess[str], what: str) -> str:
    if result.returncode:
        detail = (result.stderr or result.stdout or '').strip().splitlines()[-5:]
        raise MirrorError(f'{what} failed (exit {result.returncode}): ' + ' | '.join(detail))
    return result.stdout


class FlySsh:
    """The mirror over `fly.py ssh console`; psql connects as musicbrainz on the local socket."""

    def _ssh(self, command: str, script: str | None) -> subprocess.CompletedProcess[str]:
        if not all((ORG, DB_APP, DB_MACHINE)):
            raise MirrorError('Set FLY_ORG, FLY_APP_MB_DB and MDP_MB_DB_MACHINE')
        import fly  # the guarded wrapper beside this file
        return fly.run(['ssh', 'console', '--app', DB_APP, '--org', ORG, '--machine', DB_MACHINE,
                        '--quiet', '-C', command], input=script, text=True, capture_output=True)

    def psql(self, db: str, script: str) -> str:
        if not IDENT.match(db):
            raise ValueError(db)
        command = f'psql -X -q -A -t -v ON_ERROR_STOP=1 -U musicbrainz -d {db} -f -'
        return _check(self._ssh(command, script), f'psql on {DB_APP}/{db}')

    def volume_total(self) -> int:
        """Bytes PostgreSQL can use on the data volume: used plus available (root's reserve excluded)."""
        out = _check(self._ssh(f'df --output=used,avail -B1 {DATA_PATH}', ''), 'df on mdp-mb-db')
        used, avail = out.strip().splitlines()[-1].split()
        return int(used) + int(avail)


class LocalPsql:
    """A local psql with PGHOST/PGPORT/PGUSER/PGPASSWORD from the environment (tests)."""

    def __init__(self, total_bytes: int = 0) -> None:
        self.total_bytes = total_bytes

    def psql(self, db: str, script: str) -> str:
        result = subprocess.run(['psql', '-X', '-q', '-A', '-t', '-v', 'ON_ERROR_STOP=1', '-d', db, '-f', '-'],
                                input=script, text=True, capture_output=True, check=False)
        return _check(result, f'psql on {db}')

    def volume_total(self) -> int:
        return self.total_bytes


def status(sql: Sql, names: Names) -> dict[str, Any]:
    """Databases, bytes, the serving generation, the newest import_run and the volume size."""
    script = prelude(serving_db=names.serving, meta_db=names.meta) + r"""
\set ON_ERROR_STOP on
SELECT 'MDP_JSON ' || json_build_object(
  'databases', coalesce(json_agg(datname ORDER BY datname), '[]'::json),
  'closed', coalesce(json_agg(datname ORDER BY datname) FILTER (WHERE NOT datallowconn), '[]'::json),
  'used_bytes', coalesce(sum(pg_database_size(datname)), 0))
FROM pg_database WHERE NOT datistemplate;
SELECT EXISTS (SELECT FROM pg_database WHERE datname = :'serving_db' AND datallowconn) AS has_serving,
       EXISTS (SELECT FROM pg_database WHERE datname = :'meta_db') AS has_meta \gset
\if :has_serving
\connect :serving_db
SELECT to_regclass('mdp.generation') IS NOT NULL AS has_mdp \gset
\if :has_mdp
SELECT 'MDP_JSON ' || json_build_object('serving', (SELECT row_to_json(g) FROM mdp.generation g
  ORDER BY imported_at DESC LIMIT 1), 'serving_bytes', pg_database_size(current_database()));
\else
SELECT 'MDP_JSON ' || json_build_object('serving', NULL, 'serving_bytes', pg_database_size(current_database()));
\endif
\endif
\if :has_meta
\connect :meta_db
SELECT 'MDP_JSON ' || json_build_object(
  'run', (SELECT row_to_json(r) FROM public.import_run r ORDER BY id DESC LIMIT 1),
  'volume_bytes', (SELECT total_bytes FROM public.volume WHERE id = 1));
\endif
"""
    state = {'serving': None, 'serving_bytes': 0, 'run': None, 'volume_bytes': None}
    state.update(records(sql.psql('postgres', script)))
    return state


def ensure_meta(sql: Sql, names: Names) -> None:
    sql.psql('postgres', prelude(meta_db=names.meta) + bundle(IMPORT_DIR / 'meta.sql'))


def record_volume(sql: Sql, names: Names, total_bytes: int) -> None:
    sql.psql(names.meta, prelude(total_bytes=int(total_bytes)) + """
INSERT INTO public.volume (id, total_bytes, checked_at) VALUES (1, :total_bytes, now())
ON CONFLICT (id) DO UPDATE SET total_bytes = EXCLUDED.total_bytes, checked_at = EXCLUDED.checked_at;
""")

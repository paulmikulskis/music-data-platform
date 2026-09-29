"""Render private boot configuration; never log environment or secret SQL."""
import os
from pathlib import Path
from urllib.parse import urlsplit

root = Path('/run/mdp')
def private(name, data):
    p = root / name
    p.write_text(data)
    p.chmod(0o600)
    import pwd
    account = pwd.getpwnam('postgres')
    os.chown(p, account.pw_uid, account.pw_gid)

def quote(value):
    return "'" + value.replace("'", "''") + "'"

conf = Path('/opt/mdp/boot/postgresql.mdp.conf').read_text()
if os.environ.get('WITH_PG_LAKE') == '1':
    bucket = os.environ.get('R2_BUCKET', 'mdp-dumps')
    conf += "\nshared_preload_libraries = 'pg_extension_base,pg_stat_statements'\n"
    conf += "pg_lake_engine.host = 'host=/tmp port=5332'\n"
    conf += 'pg_lake_iceberg.default_location_prefix = ' + quote(f's3://{bucket}/iceberg') + '\n'
    if os.environ.get('R2_ACCOUNT_ID') or os.environ.get('MDP_S3_ENDPOINT'):
        endpoint = os.environ.get('MDP_S3_ENDPOINT')
        if not endpoint:
            endpoint = 'https://' + os.environ['R2_ACCOUNT_ID'] + '.r2.cloudflarestorage.com'
        url = urlsplit(endpoint)
        if url.scheme not in ('http', 'https') or not url.netloc:
            raise ValueError('Object-store endpoint must be an http(s) URL')
        key = os.environ.get('MDP_S3_ACCESS_KEY') or os.environ['R2_ACCESS_KEY_ID']
        secret = os.environ.get('MDP_S3_SECRET_KEY') or os.environ['R2_SECRET_ACCESS_KEY']
        private('duckdb-init.sql', 'CREATE OR REPLACE SECRET mdp_storage (TYPE S3, KEY_ID ' + quote(key)
                + ', SECRET ' + quote(secret) + ', ENDPOINT ' + quote(url.netloc)
                + ", REGION 'auto', URL_STYLE 'path', USE_SSL " + str(url.scheme == 'https').lower() + ');\n')
    else:
        # pg_lake stays installed; heap operation needs no object-store secret.
        private('duckdb-init.sql', '-- Object storage not configured; heap only.\n')
    private('pgduck-supervisor.conf', '''[program:pgduck_server]
command=/opt/mdp/boot/pgduck.sh
priority=10
autorestart=true
startsecs=1
startretries=20
stopsignal=TERM
stopasgroup=true
killasgroup=true
stdout_logfile=/dev/fd/1
stdout_logfile_maxbytes=0
stderr_logfile=/dev/fd/2
stderr_logfile_maxbytes=0
''')
    cache = Path('/var/lib/postgresql/pgduck-cache')
    cache.mkdir(exist_ok=True)
    import pwd
    account = pwd.getpwnam('postgres')
    os.chown(cache, account.pw_uid, account.pw_gid)
else:
    private('pgduck-supervisor.conf', '# Built WITH_PG_LAKE=0; heap-only runtime.\n')
private('postgresql.conf', conf)

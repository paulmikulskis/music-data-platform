#!/usr/bin/env python3
"""secret store-only provisioning and least-privilege import. Never log values."""
import os
import json
import subprocess
import sys

ROLES = ['migrator', 'rights_sync', 'control_rt', 'functions_rt', 'service_read', 'loader_wh', 'dbt_transform', 'workbench_wh', 'reader_wh', 'api_key_reader', 'showcase_wh']
R2 = ['R2_ACCOUNT_ID', 'R2_BUCKET', 'R2_ACCESS_KEY_ID', 'R2_SECRET_ACCESS_KEY', 'R2_ENDPOINT']
EMAIL = ['RESEND_API_KEY', 'SMTP_URL', 'MDP_EMAIL_FROM', 'MDP_EMAIL_TO']
OTLP = ['OTLP_ENDPOINT', 'OTLP_AUTH_HEADER', 'OTLP_HEADERS']
CLOUD = ['DBT_CLOUD_HOST', 'DBT_CLOUD_ACCOUNT_ID', 'DBT_CLOUD_PROJECT_ID', 'DBT_CLOUD_ENV_ID_PROD', 'DBT_CLOUD_ENV_ID_CI', 'DBT_CLOUD_TOKEN', 'DBT_CLOUD_WEBHOOK_SECRET']
MAP = {
    'mdp-postgres': ['POSTGRES_PASSWORD', 'MDP_SERVICE_URL', 'MDP_SERVICE_TOKEN'] + ['MDP_ROLE_PASSWORD_' + r.upper() for r in ROLES] + R2,
    'mdp-pg-frontend': [],
    'mdp-functions': ['MDP_CONTROL_URL', 'MDP_CONTROL_RT_URL', 'MDP_WORKBENCH_WH_URL', 'MDP_WORKBENCH_ADMIN_URL', 'MDP_READER_URL', 'MDP_WAREHOUSE_URL', 'MDP_SERVICE_READ_URL', 'MDP_SERVICE_TOKEN', 'MDP_FIXTURE_MODE', 'TYPESAFE_API_KEY', 'MDP_LITELLM_BASE_URL', 'MDP_LITELLM_KEYS', 'MDP_LITELLM_ADMIN_KEY',   'WEBSHARE_API_KEY', 'MDP_WEBSHARE_MICROCENTS_PER_BYTE', 'MDP_MB_DB_URL', 'MDP_CONTROL_API_URL', 'MDP_CONTROL_API_KEY', 'MDP_DUMP_ROOT', 'MDP_IMAGE_DIGEST'] + R2 + CLOUD + ['OTLP_ENDPOINT', 'OTLP_HEADERS'],
    'mdp-alloy': OTLP,
    'mdp-core-runner': ['MDP_PG_HOST', 'MDP_PG_PORT', 'MDP_PG_USER', 'MDP_PG_PASSWORD', 'MDP_PG_DB', 'MDP_PG_SCHEMA', 'MDP_CONTROL_RT_URL', 'MDP_SERVICE_URL', 'MDP_SERVICE_TOKEN', 'MDP_HEARTBEAT_URL'],
    # MDP_FLY_MACHINES_TOKEN (mdp-core-runner's deploy token) starts Core Retry and Replay machines.
    # MDP_READER_URL (reader_wh) lets the Explorer list warehouse relations; without it the page
    # says the warehouse reader is not reachable.
    'mdp-control-api': ['MDP_CONTROL_RT_URL', 'MDP_READER_URL', 'MDP_SERVICE_URL', 'MDP_WORKBENCH_URL', 'MDP_SERVICE_TOKEN', 'CLERK_SECRET_KEY', 'CLERK_PUBLISHABLE_KEY', 'MDP_AUTHORIZED_PARTIES', 'MDP_FLY_MACHINES_TOKEN', 'MDP_TRUSTED_BROWSER_ORIGINS', 'MDP_SHOWCASE_PEOPLE', 'MDP_SHOWCASE_LINK_SECRET', 'MDP_SHOWCASE_ORIGIN'] + EMAIL + CLOUD,
    'mdp-showcase': ['MDP_SERVICE_URL', 'MDP_SERVICE_TOKEN', 'MDP_SHOWCASE_PEOPLE', 'MDP_SHOWCASE_LINK_SECRET', 'MDP_SHOWCASE_SESSION_SECRET', 'MDP_SHOWCASE_WH_URL', 'MDP_CONTROL_RT_URL', 'MDP_SHOWCASE_READER_KEY', 'MDP_CONTROL_API_URL', 'MDP_DATA_API_URL', 'MDP_SHOWCASE_HOUSE_READER_KEY', 'MDP_SHOWCASE_ORIGIN', 'MDP_SHOWCASE_IDLE_DAYS', 'MDP_SHOWCASE_MAX_DAYS'],
    'mdp-data-api': ['MDP_READER_URL', 'MDP_API_KEY_READER_URL', 'CLERK_SECRET_KEY', 'CLERK_PUBLISHABLE_KEY', 'MDP_AUTHORIZED_PARTIES'],
}

# Deploy inputs stay on the operator machine; filter never forwards them to Fly.
DEPLOY_REQUIRED = {'mdp-showcase': ['MDP_SHOWCASE_DENY_NAMES']}

# Provider keys and optional integrations may be absent. Everything else is required.
APP_OPTIONAL = {'mdp-control-api': {'MDP_SHOWCASE_PEOPLE', 'MDP_SHOWCASE_LINK_SECRET'}}

OPTIONAL = set(R2 + CLOUD + OTLP + EMAIL + [
    'MDP_HEARTBEAT_URL',
    'TYPESAFE_API_KEY', 'MDP_LITELLM_BASE_URL', 'MDP_LITELLM_KEYS', 'MDP_LITELLM_ADMIN_KEY', 'MDP_SHOWCASE_HOUSE_READER_KEY',
    'MDP_SHOWCASE_ORIGIN', 'MDP_SHOWCASE_IDLE_DAYS', 'MDP_SHOWCASE_MAX_DAYS',
      'WEBSHARE_API_KEY',
    'MDP_WEBSHARE_MICROCENTS_PER_BYTE', 'MDP_MB_DB_URL', 'MDP_IMAGE_DIGEST',
    'CLERK_SECRET_KEY', 'CLERK_PUBLISHABLE_KEY', 'MDP_AUTHORIZED_PARTIES',
])


# Integration credentials are required only when integrations are enabled.
INTEGRATION_KEYS = {'MDP_SHOWCASE_HOUSE_READER_KEY', 'MDP_LITELLM_BASE_URL', 'MDP_LITELLM_KEYS', 'MDP_LITELLM_ADMIN_KEY'}


def check(app, values, integrations=False):
    missing = [key for key in MAP[app] if not str(values.get(key) or '').strip()]
    optional = OPTIONAL - INTEGRATION_KEYS if integrations else OPTIONAL
    optional = optional | APP_OPTIONAL.get(app, set())
    required = [key for key in missing if key not in optional]
    for key in missing:
        print(f"{'WARN optional' if key in optional else 'FAIL required'} secret {app}: {key}", file=sys.stderr)
    if app == 'mdp-control-api':
        if not any(str(values.get(key) or '').strip() for key in ('RESEND_API_KEY', 'SMTP_URL')):
            print('Email is off: set RESEND_API_KEY or SMTP_URL. See ops/fly/SECRETS.md#alert-email.', file=sys.stderr)
        for key, label in [('MDP_EMAIL_FROM', 'sender'), ('MDP_EMAIL_TO', 'recipient')]:
            if key in missing:
                print(f'Email has no {label}: set {key}. See ops/fly/SECRETS.md#alert-email.', file=sys.stderr)
    for key in DEPLOY_REQUIRED.get(app, []):
        if not str(values.get(key) or '').strip():
            required.append(key)
            print(f'FAIL deploy input {app}: {key}. Set it in secret manager; '
                  'read ops/fly/SECRETS.md#showcase.', file=sys.stderr)
    if app == 'mdp-showcase':
        print('Deploy also needs full git history and MDP_SHOWCASE_TENANT_COUNT or '
              'a working mdp tenants list connection. Follow docs/operating.md#showcase-deploy-inputs.',
              file=sys.stderr)
    if required:
        raise SystemExit(1)
    print(f'PASS required secrets {app}')


def download():
    p = subprocess.run(['secret_store', 'export', 'json'], capture_output=True, text=True, check=False)
    if p.returncode:
        raise SystemExit('secret manager download failed (output suppressed)')
    return json.loads(p.stdout)

def set_values(values):
    if not values:
        return
    # stdin prevents credentials entering arguments, shell history, or files.
    for key, value in values.items():
        p = subprocess.run(['secret_store', 'set', key], input=value, capture_output=True, text=True, check=False)
        if p.returncode:
            raise SystemExit('secret manager write failed for ' + key + ' (output suppressed)')
    print('secret manager keys stored: ' + ','.join(sorted(values)))

def provision():
    d = download()
    if d.get('FLY_ORG') != os.environ.get('FLY_ORG', 'example-org'):
        raise SystemExit('secret manager FLY_ORG must be example-org')
    values = {}
    # MDP_CONTROL_API_KEY is the promoters' control-API key; bootstrap.py stores only its sha256
    # (role 'promoter'), so the plaintext lives here and on mdp-functions alone.
    for key in ['POSTGRES_PASSWORD', 'MDP_SERVICE_TOKEN', 'MDP_CONTROL_API_KEY', 'MDP_SHOWCASE_LINK_SECRET', 'MDP_SHOWCASE_SESSION_SECRET'] + ['MDP_ROLE_PASSWORD_' + r.upper() for r in ROLES]:
        if not d.get(key):
            values[key] = subprocess.check_output(['openssl', 'rand', '-hex', '32'], text=True).strip()
    d.update(values)
    def url(role, database):
        return f"postgresql://{role}:{d['MDP_ROLE_PASSWORD_' + role.upper()]}@mdp-postgres.internal:5432/{database}?sslmode=require"
    defaults = {
        'MDP_CONTROL_URL': url('functions_rt', 'control'),
        'MDP_CONTROL_RT_URL': url('control_rt', 'control'),
        'MDP_CONTROL_DATABASE_URL': url('migrator', 'control'),
        'MDP_WAREHOUSE_URL': url('loader_wh', 'warehouse'),
        'MDP_SERVICE_READ_URL': url('service_read', 'warehouse'),
        'MDP_SHOWCASE_WH_URL': url('showcase_wh', 'warehouse'),
        'MDP_DATA_API_URL': 'http://mdp-data-api.internal:8091',
        'MDP_TRUSTED_BROWSER_ORIGINS': 'https://mdp-showcase.example.invalid',
        'MDP_READER_URL': url('reader_wh', 'warehouse'),
        'MDP_API_KEY_READER_URL': url('api_key_reader', 'control'),
        'MDP_WORKBENCH_WH_URL': url('workbench_wh', 'warehouse'),
        'MDP_WORKBENCH_ADMIN_URL': 'postgresql://postgres:' + d['POSTGRES_PASSWORD'] + '@mdp-postgres.internal:5432/warehouse?sslmode=require',
        'MDP_WORKBENCH_URL': 'http://workbench.process.mdp-functions.internal:8085',
        'MDP_PG_HOST': 'mdp-postgres.internal', 'MDP_PG_PORT': '5432',
        'MDP_PG_USER': 'dbt_transform', 'MDP_PG_PASSWORD': d['MDP_ROLE_PASSWORD_DBT_TRANSFORM'],
        'MDP_PG_DB': 'warehouse', 'MDP_PG_SCHEMA': 'dbt',
        'MDP_SERVICE_URL': 'http://api.process.mdp-functions.internal:8080',
        'MDP_CONTROL_API_URL': 'http://mdp-control-api.internal:8090',
        'MDP_DUMP_ROOT': 'file:///data/dumps',
        'MDP_FIXTURE_MODE': '0',
    }
    for key, value in defaults.items():
        if not d.get(key) or (key in ('MDP_FIXTURE_MODE',) and d.get(key) != value):
            values[key] = value
    set_values(values)


# flyctl reads `secrets import` lines with bufio.Scanner's default 64 KiB limit and ignores the
# scan error, so a longer line silently drops that key and every key after it.
FLY_LINE_MAX = 64 * 1024 - 1


def fly_line(key, value):
    """Return (line, None) that `fly secrets import` reads back as exactly value, or (None, next step).

    flyctl's parser (internal/command/secrets/parser.go) never unescapes: it removes one pair of
    surrounding double quotes, or takes a single-line value verbatim between triple double quotes.
    """
    if not isinstance(value, str):
        return None, 'it is not text; store it as a string in secret manager, then rerun the deploy'
    if '\n' in value or '\r' in value:
        return None, 'it spans lines; store it on one line in secret manager (compact JSON, or base64), then rerun the deploy'
    if '"' not in value:
        if value[:1] == "'" or value[-1:] == "'":
            return None, 'it starts or ends with a single quote; remove that quote in secret manager, then rerun the deploy'
        line = f'{key}="{value}"'
    elif '#' in value:
        return None, "it holds a double quote and '#'; in secret manager remove the '#' (in JSON, write it as \\u0023), then rerun the deploy"
    elif '"""' in value:
        return None, 'it holds three double quotes in a row; change that run in secret manager, then rerun the deploy'
    else:
        line = f'{key}="""{value}"""'
    try:
        size = len(line.encode())
    except UnicodeEncodeError:
        return None, 'it is not valid UTF-8 text; store it again in secret manager, then rerun the deploy'
    if size > FLY_LINE_MAX:
        return None, 'it is longer than the 64 KiB line fly secrets import reads; shorten it in secret manager, then rerun the deploy'
    return line, None


def emit(app):
    """Read secret store's JSON download on stdin; write the app's allowlisted keys for `fly secrets import`.

    A key absent from secret store writes no line, so Fly keeps its current value. Any value that cannot
    arrive unchanged refuses the whole import before anything is written.
    """
    try:
        values = json.loads(sys.stdin.buffer.read())
    except ValueError:
        values = None
    if not isinstance(values, dict):
        raise SystemExit('FAIL secret-map filter reads a JSON object on stdin; pipe secret_store export json into it')
    values = {key: values[key] for key in MAP[app] if key in values}
    if app == 'mdp-functions' and 'OTLP_ENDPOINT' in values:
        values['OTLP_ENDPOINT'] = 'http://mdp-alloy.internal:4318'
    lines, refused = [], []
    for key in sorted(values):
        line, step = fly_line(key, values[key])
        if line is None:
            refused.append(key)
            print(f'FAIL secret {key} cannot pass through fly secrets import unchanged; {step}', file=sys.stderr)
        else:
            lines.append(line + '\n')
    if refused:
        raise SystemExit(1)
    sys.stdout.buffer.write(''.join(lines).encode())


if __name__ == '__main__':
    if sys.argv[1] == 'provision':
        provision()
    elif sys.argv[1] == 'check':
        check(sys.argv[2], download(), integrations='--integrations' in sys.argv[3:])
    elif sys.argv[1] == 'filter':
        emit(sys.argv[2])
    elif sys.argv[1] == 'keys':
        print('\n'.join(sorted(download())))

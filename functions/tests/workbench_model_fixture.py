"""Disposable synthetic dbt graph for workbench reconstruction and rights tests."""
import json
import shutil
import sys

import psycopg
import pytest
import yaml
from mdp_functions import explore, relation_labels, workbench
from mdp_functions.settings import REPO


@pytest.fixture
def workbench_models(catalog_databases, tmp_path, monkeypatch, request):
    root = tmp_path / "fixture-repo"
    root.mkdir()
    shutil.copytree(REPO / "dbt", root / "dbt", ignore=shutil.ignore_patterns(".venv", "target", "logs", "dbt_packages"))
    for name in (".venv", "dbt_packages"):
        source = REPO / "dbt" / name
        # A fresh checkout has no package directory when dbt declares no packages.
        if source.is_dir():
            (root / "dbt" / name).symlink_to(source, target_is_directory=True)
    for name in ("functions", "ops", "control"):
        (root / name).symlink_to(REPO / name, target_is_directory=True)
    models = root / "dbt/models/test_fixture"
    models.mkdir()
    (models / "mart_scoped_fixture.sql").write_text("{{ config(materialized='table', schema='tenant_marts', tags=['cadence:daily','scope:tenant']) }} select 1 as value")
    (models / "stg_fixture_accounts__account_snapshots.sql").write_text("""
{{ config(materialized='table', schema='staging', tags=['cadence:hourly','scope:global']) }}
select * from (
 select *, row_number() over (partition by platform, platform_account_id, snapshot_at order by _landed_seq desc) as row_number
 from {{ source('raw', 'account_snapshots') }}
 where {{ mdp_context().manifest_filter('_dump_id', 'raw.account_snapshots') }}
) ranked where row_number=1
""")
    (models / "stg_control__target_history.sql").write_text("""
{{ config(materialized='table', schema='staging', tags=['cadence:hourly','scope:global']) }}
select * from {{ source('raw', 'targets') }} where {{ mdp_revision_filter('_cycle_id') }}
""")
    (models / "mart_enrichment_fixture.sql").write_text("""
{{ config(materialized='table', schema='marts', tags=['cadence:hourly','scope:global']) }}
with rows as (
 select a.platform_account_id || ':' || cast(a.snapshot_at as text) as input_ref,
 a.platform, a.platform_account_id, a.snapshot_at, a.followers, a.source_key, 'fixture-build' as _built_by,
 '["' || source_key || '"]' as _source_keys
 from {{ ref('stg_fixture_accounts__account_snapshots') }} a
 left join {{ ref('stg_control__target_history') }} t on t.id=a._target_id and t._revision_id=a._revision_id
)
{{ mdp_annotate('rows') }}
""")
    columns = ['platform', 'platform_account_id', 'snapshot_at', 'input_ref', 'followers', 'source_key', '_built_by', 'learning_eligible', 'resale_permitted', 'source_keys']
    document = {'version': 2, 'sources': [{'name': 'raw', 'schema': 'raw', 'tables': [{'name': 'account_snapshots'}]}],
                'models': [{'name': 'mart_enrichment_fixture', 'columns': [{'name': name} for name in columns]}]}
    # Extend the existing raw declaration instead of duplicating its source name.
    for path in (root / 'dbt/models').rglob('*.yml'):
        data = yaml.safe_load(path.read_text()) or {}
        for source in data.get('sources', []):
            if source['name'] == 'raw':
                source['tables'].append({'name': 'account_snapshots'})
                path.write_text(yaml.safe_dump(data, sort_keys=False))
    document.pop('sources')
    (models / 'fixture.yml').write_text(yaml.safe_dump(document))
    original = relation_labels.load
    labels = original()
    labels['staging.stg_control__target_history'] = dict(labels['raw.targets'], shared_privacy=labels['raw.targets']['privacy'])
    common = dict(labels['raw.playlist_items'], tenant='global', category='public platform data')
    raw_columns = ['platform', 'platform_account_id', 'handle', 'followers', 'snapshot_at', 'source_key', '_dump_id', '_landed_seq', '_target_id', '_revision_id']
    for relation, names in [('raw.account_snapshots', raw_columns), ('staging.stg_fixture_accounts__account_snapshots', raw_columns), ('marts.mart_enrichment_fixture', columns)]:
        labels[relation] = dict(common, columns=dict.fromkeys(names, 'Synthetic fixture.'), privacy=dict.fromkeys(names, 'non_personal'), shared_privacy=dict.fromkeys(names, 'non_personal'))
    for module in list(sys.modules.values()):
        if module and getattr(module, '__name__', '').startswith(('mdp_functions', 'test_workbench')) and getattr(module, 'load', None) is original:
            monkeypatch.setattr(module, 'load', lambda: labels)
    monkeypatch.setattr(explore, 'load', lambda: labels)
    monkeypatch.setattr(workbench, 'REPO', root)
    monkeypatch.setattr(request.module, 'REPO', root, raising=False)
    with psycopg.connect(catalog_databases['admin_warehouse']) as conn:
        for relation in ['staging.stg_control__target_history', 'raw.account_snapshots', 'staging.stg_fixture_accounts__account_snapshots', 'marts.mart_enrichment_fixture']:
            conn.execute('INSERT INTO catalog.label_definitions VALUES (%s,%s) ON CONFLICT(relation) DO UPDATE SET labels=EXCLUDED.labels', (relation, json.dumps(labels[relation])))
    return root

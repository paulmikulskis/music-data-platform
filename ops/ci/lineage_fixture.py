#!/usr/bin/env python3
"""Check built DuckDB mart rows against their declared lineage and rights registry."""
import json
import sys
from pathlib import Path

import duckdb
from review_gate import lineage


def quoted(name):
    return '"' + name.replace('"', '""') + '"'


def main(database):
    manifest = json.loads(Path('dbt/target/manifest.json').read_text())
    con = duckdb.connect(database, read_only=True)
    seed = manifest['nodes']['seed.music_data_platform.rights_registry']
    registry_relation = quoted(seed['schema']) + '.' + quoted(seed['alias'])
    registry = {key: (learn, resale) for key, learn, resale in con.execute(
        f'select source_key, learning_eligible, resale_permitted from {registry_relation}'
    ).fetchall()}
    marts = rows = 0
    for node in manifest['nodes'].values():
        if node.get('resource_type') != 'model' or not node.get('config', {}).get('meta', {}).get('grain'):
            continue
        relation = quoted(node['schema']) + '.' + quoted(node['alias'])
        floor = lineage(manifest, node['name']) or set()
        for source_keys, learning, resale in con.execute(
            f'select source_keys, learning_eligible, resale_permitted from {relation}'
        ).fetchall():
            keys = json.loads(source_keys)
            assert isinstance(keys, list) and all(isinstance(key, str) for key in keys), relation
            assert floor <= set(keys), f'{relation}: missing upstream source'
            assert learning is not None and resale is not None, f'{relation}: null rights flag'
            assert not learning or (keys and all(registry.get(key, (False, False))[0] for key in keys)), f'{relation}: unsafe learning flag'
            assert not resale or (keys and all(registry.get(key, (False, False))[1] for key in keys)), f'{relation}: unsafe resale flag'
            if 'scope:tenant' in node['config'].get('tags', []):
                assert not learning, f'{relation}: tenant learning flag'
            rows += 1
        marts += 1
    assert marts and rows, 'Build the synthetic CI warehouse before checking lineage'
    print(f'PASS runtime lineage: {marts} marts, {rows} rows, 0 violations')


if __name__ == '__main__':
    main(sys.argv[1])

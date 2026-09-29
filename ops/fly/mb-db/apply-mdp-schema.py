#!/usr/bin/env python3
"""One-time handoff of the live mirror to the generation design.

The first import loaded export 20260923-002121 into musicbrainz_db before generations existed. This
applies the same steps a refresh import runs after the upstream load, over the guarded SSH path:
mdp_meta and its tables, the volume size, finalize.sql (reader grants, mdp-schema.sql with the
trigram and tail-id indexes, the mdp.generation row), verify.sql, validated_at, and one import_run
row recording the serving generation as promoted. It never drops, renames or reloads anything, and
running it again recomputes the same generation row. Building the trigram index on the full mirror
takes a while; readers keep working because CREATE INDEX blocks only writes.

`--analyze-url` runs only mdp-schema.sql's ANALYZE musicbrainz.url on the serving database: the
statistics the tail lookups plan with, without finalize rebuilding mdp.tracked_url or re-validating
the generation. It is idempotent and blocks no reader.

    python3 ops/fly/mb-db/apply-mdp-schema.py --dry-run
    python3 ops/fly/mb-db/apply-mdp-schema.py
    python3 ops/fly/mb-db/apply-mdp-schema.py --analyze-url
"""
from __future__ import annotations

import argparse
import sys

import mirror
from mirror import Names, Sql, bundle, prelude, records

GENERATION = '20260923-002121'
SCHEMA_SEQUENCE = 31  # MUSICBRAINZ_DB_SCHEMA_SEQUENCE of the pinned v-2026-09-21.0 image


def handoff(sql: Sql, names: Names, generation: str, schema_sequence: int, out=print) -> dict:
    if not mirror.STAMP.match(generation):
        raise ValueError(generation)
    state = mirror.status(sql, names)
    if names.serving not in state['databases']:
        raise mirror.MirrorError(f'{names.serving} does not exist; nothing to hand off')
    serving = state['serving']
    if serving and serving['generation'] != generation:
        raise mirror.MirrorError(f"{names.serving} already records generation {serving['generation']}")
    out(f'1/6 mdp_meta: {names.meta} with import_run and volume, reader grants')
    mirror.ensure_meta(sql, names)
    out('2/6 volume: record the capacity PostgreSQL can use')
    mirror.record_volume(sql, names, sql.volume_total())
    out(f'3/6 finalize {names.serving}: reader grants, mdp schema, trigram and tail-id indexes, generation row')
    sql.psql(names.serving, prelude(generation=generation) + bundle(mirror.IMPORT_DIR / 'finalize.sql'))
    out(f'4/6 verify {names.serving}')
    verified = sql.psql(names.serving, prelude(generation=generation, expected_schema_sequence=schema_sequence)
                        + bundle(mirror.IMPORT_DIR / 'verify.sql'))
    if f'MDP_VERIFIED {generation}' not in verified:
        raise mirror.MirrorError('verify.sql did not report MDP_VERIFIED')
    out('5/6 validated_at')
    sql.psql(names.serving, prelude(generation=generation) + """
UPDATE mdp.generation SET validated_at = now() WHERE generation = :'generation';
""")
    out('6/6 import_run: the serving generation, promoted')
    row = records(sql.psql(names.meta, prelude(generation=generation, serving_db=names.serving) + """
INSERT INTO public.import_run (generation, target_db, state, phase, finished_at, message)
SELECT :'generation', :'serving_db', 'promoted', 'handoff', now(),
       'initial import recorded by apply-mdp-schema; mdp schema, indexes and generation row applied'
WHERE NOT EXISTS (SELECT FROM public.import_run WHERE generation = :'generation'
                  AND target_db = :'serving_db' AND state = 'promoted')
RETURNING 'MDP_JSON ' || json_build_object('run_id', id);
"""))
    return mirror.status(sql, names) | row


def analyze_url(sql: Sql, names: Names, out=print) -> None:
    out(f'ANALYZE musicbrainz.url in {names.serving}: statistics for url_idx_mdp_tail_id')
    sql.psql(names.serving, 'ANALYZE musicbrainz.url;\n')


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--generation', default=GENERATION, help='export stamp the live database holds')
    parser.add_argument('--schema-sequence', type=int, default=SCHEMA_SEQUENCE)
    parser.add_argument('--dry-run', action='store_true', help='print the steps and touch nothing')
    parser.add_argument('--analyze-url', action='store_true',
                        help='only ANALYZE musicbrainz.url on the serving database')
    args = parser.parse_args(argv)
    names = Names()
    if args.analyze_url:
        if args.dry_run:
            print(f'Would run ANALYZE musicbrainz.url in {names.serving} on {mirror.DB_APP} as musicbrainz')
            return 0
        analyze_url(mirror.FlySsh(), names)
        print(f'Done: musicbrainz.url analyzed in {names.serving}')
        return 0
    if args.dry_run:
        print(f'Would run on {mirror.DB_APP} machine {mirror.DB_MACHINE} over fly.py ssh console, as musicbrainz:')
        print(f'  meta.sql in postgres -> {names.meta}; df {mirror.DATA_PATH} -> volume.total_bytes')
        print(f'  finalize.sql, verify.sql (schema sequence {args.schema_sequence}), validated_at in {names.serving}')
        print(f'  import_run: {args.generation} -> {names.serving}, promoted')
        return 0
    result = handoff(mirror.FlySsh(), names, args.generation, args.schema_sequence)
    serving = result['serving'] or {}
    print(f"Done: {names.serving} serves {serving.get('generation')} validated at {serving.get('validated_at')}; "
          f"import_run {result.get('run_id')}; {result['used_bytes']} of {result['volume_bytes']} bytes used")
    return 0


if __name__ == '__main__':
    try:
        sys.exit(main())
    except mirror.MirrorError as error:
        print(f'reference_import_failed: handoff stopped: {error}')
        sys.exit(1)

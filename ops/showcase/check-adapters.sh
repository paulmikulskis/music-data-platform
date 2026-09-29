#!/usr/bin/env bash
# Build the reviewed room inputs in an initialized, disposable local warehouse.
set -euo pipefail
cd "$(dirname "$0")/../.."
export MDP_PG_HOST=127.0.0.1 MDP_PG_PORT="${MDP_PG_PORT:-5432}"
export MDP_PG_USER=dbt_transform MDP_PG_PASSWORD=dbt_transform MDP_PG_DB=warehouse MDP_PG_SCHEMA=dbt
export MDP_WAREHOUSE_URL="postgresql://loader_wh:loader_wh@127.0.0.1:$MDP_PG_PORT/warehouse?sslmode=prefer"
export DBT_MDP_SCOPE=global
scratch=$(mktemp -d /tmp/showcase-adapters.XXXXXX)
trap 'rm -rf "$scratch"' EXIT
uv run --project functions python - "$scratch" <<'PY'
import os
import sys
from pathlib import Path
import yaml
from mdp_functions.exporter import ensure_raw
from mdp_functions.settings import Settings
profile = yaml.safe_load(Path('dbt/profiles/profiles.example.yml').read_text())
profile['music_data_platform']['outputs']['pg_local']['sslmode'] = 'prefer'
Path(sys.argv[1], 'profiles.yml').write_text(yaml.safe_dump(profile))
ensure_raw(os.environ['MDP_WAREHOUSE_URL'], Settings().schema_root)
# Session creation installs these same generated raw copies before a Workbench query.
import psycopg
from mdp_functions.explore import install
with psycopg.connect(f"postgresql://postgres:postgres@127.0.0.1:{os.environ['MDP_PG_PORT']}/warehouse") as conn:
    install(conn)
PY
args=(--project-dir dbt --profiles-dir "$scratch" --target pg_local --vars '{"dry_run":true}')
uv run --project dbt dbt deps "${args[@]}"
uv run --project dbt dbt seed "${args[@]}" --full-refresh
uv run --project dbt dbt build "${args[@]}" --select +stg_billboard__chart_entries +mart_editorial_entries +mart_playlist_coverage +mart_playlist_membership_current +stg_lb__fresh_releases +mart_top_movers_current +mart_arrivals_current +mart_early_signals_current +mart_song_aliases +mart_search_index --indirect-selection cautious
MDP_SHOWCASE_QUERY_TEST_URL="postgresql://showcase_wh:showcase_wh@127.0.0.1:$MDP_PG_PORT/warehouse?sslmode=prefer" \
MDP_SHOWCASE_ADAPTER_TEST_URL="postgresql://showcase_wh:showcase_wh@127.0.0.1:$MDP_PG_PORT/warehouse?sslmode=prefer" \
MDP_WORKBENCH_EXAMPLE_TEST_URL="postgresql://workbench_wh:workbench_wh@127.0.0.1:$MDP_PG_PORT/warehouse?sslmode=prefer" \
pnpm --dir control --filter @mdp/showcase exec vitest run --no-file-parallelism test/music-queries.test.ts test/search-query.test.ts test/peek-sql.test.ts test/team-question.test.ts
MDP_WORKBENCH_EXAMPLE_TEST_URL="postgresql://workbench_wh:workbench_wh@127.0.0.1:$MDP_PG_PORT/warehouse?sslmode=prefer" \
pnpm --dir control exec vitest run apps/control-api/test/workbench-examples.test.ts
MDP_SHOWCASE_QUERY_TEST_URL="postgresql://showcase_wh:showcase_wh@127.0.0.1:$MDP_PG_PORT/warehouse?sslmode=prefer" \
MDP_WORKBENCH_EXAMPLE_TEST_URL="postgresql://workbench_wh:workbench_wh@127.0.0.1:$MDP_PG_PORT/warehouse?sslmode=prefer" \
uv run --project functions python ops/showcase/lineage/permissions.py --output "${MDP_SHOWCASE_PERMISSION_OUTPUT:-$scratch/permissions.generated.json}"
printf 'Reviewed adapters pass. The inventory is ops/showcase/queries.json.\n'

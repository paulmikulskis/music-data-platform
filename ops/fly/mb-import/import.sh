#!/usr/bin/env bash
# Import the newest MusicBrainz CC0 full export into its own generation database.
#   MDP_IMPORT_MODE=initial  loads musicbrainz_db on an empty server; it serves when validated.
#   MDP_IMPORT_MODE=refresh  loads musicbrainz_next beside the serving database; refresh.py promotes
#                            it only after this script records state 'validated'.
# Progress goes to import_run in mdp_meta. A failure records state 'failed' with
# reference_import_failed and exits nonzero; nothing retries automatically.
set -euo pipefail
umask 077

MODE=${MDP_IMPORT_MODE:-initial}
SERVING_DB=${MDP_SERVING_DB:-musicbrainz_db}
NEXT_DB=${MDP_NEXT_DB:-musicbrainz_next}
META_DB=${MDP_META_DB:-mdp_meta}
DUMP_DIR=${MDP_DUMP_DIR:-/media/dbdump}
SQL_DIR=${MDP_SQL_DIR:-/opt/mdp/mb-import}
MB_SERVER_DIR=${MDP_MB_SERVER_DIR:-/musicbrainz-server}
RUN_ID=${MDP_IMPORT_RUN_ID:-}
case "$MODE" in
  initial) TARGET_DB=$SERVING_DB ;;
  refresh) TARGET_DB=$NEXT_DB ;;
  *) echo "reference_import_failed: unknown MDP_IMPORT_MODE=$MODE"; exit 64 ;;
esac
for name in "$TARGET_DB" "$META_DB"; do
  [[ $name =~ ^[a-z_][a-z0-9_]*$ ]] || { echo "reference_import_failed: bad database name $name"; exit 64; }
done
[[ -z $RUN_ID || $RUN_ID =~ ^[0-9]+$ ]] || { echo 'reference_import_failed: bad MDP_IMPORT_RUN_ID'; exit 64; }
: "${POSTGRES_PASSWORD:?Fly secret POSTGRES_PASSWORD is not set on mdp-mb-import}"
: "${MB_READER_PASSWORD:?Fly secret MB_READER_PASSWORD is not set on mdp-mb-import}"
export PGHOST="$MUSICBRAINZ_POSTGRES_SERVER" PGUSER="$POSTGRES_USER" PGPASSWORD="$POSTGRES_PASSWORD"
# The patched upstream DBDefs.pm reads the database InitDb.pl creates and loads from MDP_TARGET_DB.
export MDP_TARGET_DB="$TARGET_DB"
PSQL=(psql -X -q -A -t -v ON_ERROR_STOP=1)
STAMP_RE='^[0-9]{8}-[0-9]{6}$'

mkdir -p "$DUMP_DIR"
# Explicit commercial intent for the upstream fetch prompt.
touch "$DUMP_DIR/.for-commercial-use"
if [[ -f $DUMP_DIR/mdp-import.started ]]; then
  echo 'reference_import_failed: prior attempt exists on this volume; inspect before retrying'
  exit 1
fi
date -u +%FT%TZ > "$DUMP_DIR/mdp-import.started"
exec > >(tee -a "$DUMP_DIR/mdp-import.log") 2>&1

PHASE=start
meta() { "${PSQL[@]}" -d "$META_DB" "$@"; }
set_phase() {
  PHASE=$1
  echo "MDP_IMPORT_PHASE $PHASE $(date -u +%FT%TZ)"
  meta -c "UPDATE import_run SET phase = '$PHASE' WHERE id = $RUN_ID AND state = 'running'"
}
on_exit() {
  local rc=$?
  if (( rc != 0 )); then
    date -u +%FT%TZ > "$DUMP_DIR/mdp-import.failed"
    echo "reference_import_failed phase=$PHASE exit=$rc"
    if [[ -n $RUN_ID ]]; then
      meta -c "UPDATE import_run SET state = 'failed', finished_at = now(),
               message = 'reference_import_failed: $PHASE exited $rc; see mdp-import.log on the import volume'
               WHERE id = $RUN_ID AND state = 'running'" || true
    fi
  fi
}
trap on_exit EXIT
echo "MDP_IMPORT_STARTED $(cat "$DUMP_DIR/mdp-import.started") mode=$MODE target=$TARGET_DB"

"${PSQL[@]}" -d postgres -v meta_db="$META_DB" -f "$SQL_DIR/meta.sql"
if [[ -z $RUN_ID ]]; then
  GENERATION=$(wget -q -O - "$MUSICBRAINZ_BASE_DOWNLOAD_URL/data/fullexport/LATEST")
  [[ $GENERATION =~ $STAMP_RE ]] || { echo "reference_import_failed: upstream LATEST is not an export stamp"; exit 1; }
  RUN_ID=$(meta -c "INSERT INTO import_run (generation, target_db, state, phase)
                    VALUES ('$GENERATION', '$TARGET_DB', 'running', 'start') RETURNING id")
else
  GENERATION=$(meta -c "SELECT generation FROM import_run
                        WHERE id = $RUN_ID AND state = 'running' AND target_db = '$TARGET_DB'")
  [[ $GENERATION =~ $STAMP_RE ]] || { echo "reference_import_failed: run $RUN_ID is not a running import into $TARGET_DB"; exit 1; }
fi
echo "MDP_IMPORT_RUN $RUN_ID generation=$GENERATION"

set_phase preflight
if [[ -n $("${PSQL[@]}" -d postgres -c "SELECT 1 FROM pg_database WHERE datname = '$TARGET_DB'") ]]; then
  echo "reference_import_failed: $TARGET_DB already exists; a generation is only ever loaded into a new database"
  exit 1
fi
# Upstream createdb.sh adds --createdb only when this exits 1 (no such database). Exit 0 would mean
# DBDefs resolves MAINTENANCE to an existing database such as the serving one: refuse.
set +e
carton exec -- "$MB_SERVER_DIR/script/database_exists" MAINTENANCE
rc=$?
set -e
if (( rc != 1 )); then
  echo "reference_import_failed: MAINTENANCE does not resolve to a new $TARGET_DB (database_exists exit $rc)"
  exit 1
fi

set_phase fetch
fetch-dump.sh replica --base-download-url "$MUSICBRAINZ_BASE_DOWNLOAD_URL" \
  --wget-options '--progress=dot:giga --timeout=60 --tries=5'
FETCHED=$(<"$DUMP_DIR/LATEST")
[[ $FETCHED =~ $STAMP_RE ]] || { echo 'reference_import_failed: fetched LATEST is not an export stamp'; exit 1; }
if [[ $FETCHED != "$GENERATION" ]]; then
  echo "Upstream published $FETCHED after the run started for $GENERATION; importing $FETCHED"
  GENERATION=$FETCHED
  meta -c "UPDATE import_run SET generation = '$GENERATION' WHERE id = $RUN_ID AND state = 'running'"
fi

set_phase import
cd "$MB_SERVER_DIR"
# Without -fetch, createdb.sh loads the checksum-verified archives already in /media/dbdump.
createdb.sh
if [[ -z $("${PSQL[@]}" -d postgres -c "SELECT 1 FROM pg_database WHERE datname = '$TARGET_DB'") ]]; then
  echo "reference_import_failed: the upstream import did not create $TARGET_DB"
  exit 1
fi

set_phase finalize
# finalize.sql sets the reader password; its output stays in a private log, never the import log.
if ! "${PSQL[@]}" -d "$TARGET_DB" -v generation="$GENERATION" -f "$SQL_DIR/finalize.sql" \
    >"$DUMP_DIR/finalize.private.log" 2>&1; then
  echo 'reference_import_failed: finalization failed; inspect finalize.private.log with redaction'
  exit 1
fi

set_phase verify
psql -X -q -v ON_ERROR_STOP=1 -d "$TARGET_DB" -v generation="$GENERATION" \
  -v expected_schema_sequence="$MUSICBRAINZ_DB_SCHEMA_SEQUENCE" -f "$SQL_DIR/verify.sql" \
  | tee "$DUMP_DIR/mdp-verification.txt"
grep -q "MDP_VERIFIED $GENERATION" "$DUMP_DIR/mdp-verification.txt"
READER=$(PGUSER=mb_reader PGPASSWORD="$MB_READER_PASSWORD" "${PSQL[@]}" -d "$TARGET_DB" \
  -c 'SELECT generation FROM mdp.generation')
echo "$READER" > "$DUMP_DIR/reader-check.txt"
[[ $READER == "$GENERATION" ]] || { echo 'reference_import_failed: reader cannot read mdp.generation'; exit 1; }

"${PSQL[@]}" -d "$TARGET_DB" -c "UPDATE mdp.generation SET validated_at = now() WHERE generation = '$GENERATION'"
if [[ $MODE == initial ]]; then
  UPDATED=$(meta -c "UPDATE import_run SET state = 'promoted', phase = 'serving', finished_at = now(),
           message = 'initial import serving as $TARGET_DB' WHERE id = $RUN_ID AND state = 'running' RETURNING id")
else
  UPDATED=$(meta -c "UPDATE import_run SET state = 'validated', phase = 'validated',
           message = 'validated in $TARGET_DB; waiting for promotion' WHERE id = $RUN_ID AND state = 'running' RETURNING id")
fi
[[ $UPDATED == "$RUN_ID" ]] || { echo "reference_import_failed: run $RUN_ID is no longer running, so it will not be promoted"; exit 1; }
PHASE=done
date -u +%FT%TZ > "$DUMP_DIR/mdp-import.complete"
echo "MDP_IMPORT_COMPLETE $(cat "$DUMP_DIR/mdp-import.complete") run=$RUN_ID generation=$GENERATION mode=$MODE"
df -h "$DUMP_DIR"

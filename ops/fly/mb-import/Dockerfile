# Build context is ops/fly: refresh.py runs `fly machine run . --dockerfile mb-import/Dockerfile`
# there, so finalize.sql can include ../mb-db/mdp-schema.sql in the image as it does in the repo.
FROM metabrainz/musicbrainz-docker-musicbrainz:v-2026-09-21.0-build0
USER root
# Keep upstream fetch-dump.sh and its checksum verification; restrict the archive list to the five
# requested bundles. No edit/editor archive is fetched.
RUN sed -i '/^[[:space:]]*mbdump-event-art-archive.tar.bz2$/d; /^[[:space:]]*mbdump-wikidocs.tar.bz2$/d' /usr/local/bin/createdb.sh /usr/local/bin/fetch-dump.sh
# Upstream cleanup removes arbitrary files in /media/dbdump on a new timestamp.
# Preserve our attempt marker and log so restart protection and evidence survive.
RUN sed -i 's/! -path "$DB_DUMP_DIR\/LATEST"/! -name "mdp-*" ! -path "$DB_DUMP_DIR\/LATEST"/' /usr/local/bin/fetch-dump.sh
# Upstream DBDefs.pm hard-codes musicbrainz_db for READWRITE and READONLY (MAINTENANCE falls back to
# READWRITE). Read it from MDP_TARGET_DB so a refresh loads musicbrainz_next beside the serving
# database. The build fails unless exactly those two lines changed.
RUN sed -i 's|^\([[:space:]]*database[[:space:]]*=> \)"musicbrainz_db",|\1$ENV{MDP_TARGET_DB} // "musicbrainz_db",|' /musicbrainz-server/lib/DBDefs.pm \
 && test "$(grep -c 'MDP_TARGET_DB' /musicbrainz-server/lib/DBDefs.pm)" = 2
COPY mb-import/import.sh mb-import/meta.sql mb-import/finalize.sql mb-import/verify.sql /opt/mdp/mb-import/
COPY mb-db/mdp-schema.sql /opt/mdp/mb-db/
CMD ["bash", "/opt/mdp/mb-import/import.sh"]

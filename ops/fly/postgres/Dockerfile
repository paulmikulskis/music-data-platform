# syntax=docker/dockerfile:1
FROM postgres:17-bookworm AS builder
# Set WITH_PG_LAKE=0 only for the explicit heap fallback.
ARG WITH_PG_LAKE=1
# v3.5.1: latest upstream release verified 2026-09-18; immutable source pin.
ARG PG_LAKE_REF=2dfa035011b041ffcf2e31ba82d5c20ded3c9196
ARG BUILD_JOBS=4
ENV PATH=/usr/lib/postgresql/17/bin:$PATH
RUN mkdir -p /opt/pg-lake-runtime && if [ "$WITH_PG_LAKE" = 1 ]; then \
    apt-get update && apt-get install -y --no-install-recommends \
    build-essential postgresql-server-dev-17 git ca-certificates curl zip unzip tar \
    cmake ninja-build flex bison pkg-config python3-dev libtool autoconf automake \
    libssl-dev libxml2-dev libxslt1-dev libicu-dev liblz4-dev liblzma-dev \
    libsnappy-dev libjansson-dev libcurl4-openssl-dev libprotobuf-c-dev \
    protobuf-c-compiler uuid-dev libossp-uuid-dev libreadline-dev zlib1g-dev libkrb5-dev libpam0g-dev; fi
RUN if [ "$WITH_PG_LAKE" = 1 ]; then apt-get update && apt-get install -y --no-install-recommends libselinux1-dev libzstd-dev; fi
RUN if [ "$WITH_PG_LAKE" = 1 ]; then \
    git init /src && cd /src && git remote add origin https://github.com/Snowflake-Labs/pg_lake.git && \
    git fetch --depth 1 origin "$PG_LAKE_REF" && git checkout FETCH_HEAD && \
    sed -i '/^PG_LDFLAGS  =/s/$/ -L$(shell $(PG_CONFIG) --pkglibdir)/' pgduck_server/Makefile && \
    PG_CONFIG=/usr/lib/postgresql/17/bin/pg_config MAKEFLAGS="NCORES=$BUILD_JOBS" PGCOMPAT_BUILD_CONFIG=Release ./install.sh --deps-dir /opt/deps --jobs "$BUILD_JOBS"; fi
# Collect installed runtime files and their transitive shared-library dependencies.
RUN if [ "$WITH_PG_LAKE" = 1 ]; then \
    mkdir -p /opt/pg-lake-runtime/usr/lib/postgresql/17 /opt/pg-lake-runtime/usr/share/postgresql/17 && \
    cp -a /usr/lib/postgresql/17/lib /usr/lib/postgresql/17/bin /opt/pg-lake-runtime/usr/lib/postgresql/17/ && \
    cp -a /usr/share/postgresql/17/extension /opt/pg-lake-runtime/usr/share/postgresql/17/ && \
    find /usr/lib/postgresql/17 -type f -exec sh -c 'ldd "$1" 2>/dev/null || true' _ {} \; \
      | awk '/=> \// {print $3} /^\// {print $1}' | sort -u \
      | xargs -r -n1 readlink -f | sort -u \
      | xargs -r -I{} cp --parents -L '{}' /opt/pg-lake-runtime/; fi

FROM postgres:17-bookworm
# Set WITH_PG_LAKE=0 only for the explicit heap fallback.
ARG WITH_PG_LAKE=1
ENV WITH_PG_LAKE=$WITH_PG_LAKE PGDUCK_MEMORY_LIMIT=1GB PGDATA=/var/lib/postgresql/data/pgdata
RUN apt-get update && apt-get install -y --no-install-recommends \
    "postgresql-plpython3-17=$PG_VERSION" python3 python3-pip supervisor openssl ca-certificates \
    && pip3 install --no-cache-dir --break-system-packages httpx==0.28.1 'psycopg[binary]==3.3.5' sqlglot==30.17.0 \
    && python3 -c 'import httpx' && rm -rf /var/lib/apt/lists/*
COPY --from=builder /opt/pg-lake-runtime/ /
# Recreate SONAME links/cache after canonicalizing merged-/usr library paths.
RUN ldconfig
COPY ops/fly/postgres/supervisord.conf /etc/supervisor/supervisord.conf
COPY ops/fly/postgres/boot/ /opt/mdp/boot/
COPY functions/src/mdp_functions/sandbox.py functions/src/mdp_functions/sandbox_policy.py /opt/mdp/boot/mdp_functions/
COPY functions/src/mdp_functions/errors.py functions/src/mdp_functions/error_catalog.json /opt/mdp/boot/mdp_functions/
COPY functions/src/mdp_functions/relation_labels.py functions/src/mdp_functions/query_labels.py functions/src/mdp_functions/relation_labels.json /opt/mdp/boot/mdp_functions/
RUN touch /opt/mdp/boot/mdp_functions/__init__.py
# These two control-db files are the single source of database and role definitions.
COPY control/packages/control-db/sql/databases.sql control/packages/control-db/sql/roles.sql control/packages/control-db/sql/showcase-role.sql /opt/mdp/sql/
COPY ops/fly/postgres/boot/init/00-mdp-init.sh /docker-entrypoint-initdb.d/00-mdp-init.sh
RUN chmod +x /opt/mdp/boot/*.sh /docker-entrypoint-initdb.d/00-mdp-init.sh
RUN if [ "$WITH_PG_LAKE" = 1 ]; then \
    /usr/lib/postgresql/17/bin/pgduck_server --help && \
    /opt/mdp/boot/pgduck.sh --check_cli_params_only; fi
HEALTHCHECK --interval=10s --timeout=5s --start-period=60s --retries=6 CMD /opt/mdp/boot/healthcheck.sh
ENTRYPOINT ["/opt/mdp/boot/entrypoint.sh"]

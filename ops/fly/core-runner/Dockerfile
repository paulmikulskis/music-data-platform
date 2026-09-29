# Build from repository root; both projects are used by ops/run.sh.
FROM python:3.12-slim
COPY --from=ghcr.io/astral-sh/uv:0.10.12 /uv /usr/local/bin/uv
ENV PYTHONUNBUFFERED=1 DBT_PROFILES_DIR=/app/dbt/profiles UV_NO_DEV=1 UV_FROZEN=1
WORKDIR /app
COPY dbt/pyproject.toml dbt/uv.lock dbt/.python-version ./dbt/
RUN uv sync --project dbt --frozen --no-dev
COPY functions/pyproject.toml functions/uv.lock ./functions/
COPY functions/src ./functions/src
RUN uv sync --project functions --frozen --no-dev
COPY dbt ./dbt
COPY ops/run.sh ops/runner_lock.py ops/runner_restore.py ops/heartbeat.py ./ops/
COPY ops/fly/core-runner ./ops/fly/core-runner
RUN cp dbt/profiles/profiles.example.yml dbt/profiles/profiles.yml
ENTRYPOINT ["./ops/run.sh"]
CMD ["daily"]

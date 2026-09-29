FROM python:3.12-slim
COPY --from=ghcr.io/astral-sh/uv:0.10.12 /uv /usr/local/bin/uv
RUN apt-get update && apt-get install -y --no-install-recommends gosu git gh util-linux && rm -rf /var/lib/apt/lists/*
WORKDIR /app/functions
COPY functions/pyproject.toml functions/uv.lock ./
COPY functions/src ./src
RUN uv sync --frozen --no-dev && useradd --create-home --uid 10001 mdp && mkdir -p schemas && chown -R mdp:mdp /app
COPY dbt/ /app/dbt/
RUN cp /app/dbt/profiles/profiles.example.yml /app/dbt/profiles/profiles.yml && uv sync --project /app/dbt --frozen --no-dev && chown -R mdp:mdp /app/dbt
COPY ops/fly/functions/ /opt/mdp/
ARG MDP_BUILD_SHA
ENV MDP_BUILD_SHA=$MDP_BUILD_SHA
ENV PATH="/app/functions/.venv/bin:$PATH"
# Fly private addresses use IPv6; local module execution defaults to 0.0.0.0.
ENV MDP_WORKBENCH_HOST="::"
ENTRYPOINT ["/opt/mdp/entrypoint.sh"]
CMD ["python", "/opt/mdp/serve.py"]

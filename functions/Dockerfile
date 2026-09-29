FROM python:3.12-slim
COPY --from=ghcr.io/astral-sh/uv:0.10.12 /uv /usr/local/bin/uv
WORKDIR /app/functions
COPY pyproject.toml uv.lock ./
COPY src ./src
RUN uv sync --frozen --no-dev && useradd --create-home --uid 10001 mdp \
    && mkdir -p /app/functions/schemas /app/functions/.dumps \
    && chown -R mdp:mdp /app/functions
ENV PATH="/app/functions/.venv/bin:$PATH"
USER mdp
EXPOSE 8080
CMD ["mdp", "serve"]

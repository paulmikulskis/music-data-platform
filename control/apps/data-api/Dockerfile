FROM node:22-bookworm-slim
RUN corepack enable && corepack prepare pnpm@10.28.0 --activate
WORKDIR /app/control
COPY control/ ./
RUN pnpm install --frozen-lockfile
ARG MDP_BUILD_SHA
ENV MDP_BUILD_SHA=$MDP_BUILD_SHA
USER node
CMD ["pnpm", "--filter", "@mdp/data-api", "start"]

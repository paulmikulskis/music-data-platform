FROM node:22-bookworm-slim AS build
ARG MDP_DEPLOY_REVISION
RUN corepack enable && corepack prepare pnpm@10.28.0 --activate
WORKDIR /app/control
COPY control/ ./
COPY ops/showcase/ /app/ops/showcase/
RUN pnpm install --frozen-lockfile
ENV NEXT_TELEMETRY_DISABLED=1
RUN pnpm exec tsx ../ops/showcase/validate-build.mts
RUN pnpm --filter @mdp/showcase build
FROM node:22-bookworm-slim
ARG MDP_DEPLOY_REVISION
LABEL org.opencontainers.image.revision=$MDP_DEPLOY_REVISION
WORKDIR /app
ENV NODE_ENV=production NEXT_TELEMETRY_DISABLED=1 HOSTNAME=0.0.0.0 PORT=3000
COPY --from=build --chown=node:node /app/control/apps/showcase/.next/standalone ./
COPY --from=build --chown=node:node /app/control/apps/showcase/.next/static ./apps/showcase/.next/static
COPY --from=build --chown=node:node /app/control/apps/showcase/public ./apps/showcase/public
COPY --from=build --chown=node:node /app/control/apps/showcase/lib/lineage.generated.json ./apps/showcase/artifacts/lineage.generated.json
COPY --from=build --chown=node:node /app/control/apps/showcase/lib/stack.generated.json ./apps/showcase/artifacts/stack.generated.json
COPY --from=build --chown=node:node /app/control/apps/showcase/lib/artifacts.build.json ./apps/showcase/artifacts/artifacts.build.json
COPY --from=build --chown=node:node /app/control/apps/showcase/lib/links.generated.json ./apps/showcase/artifacts/links.generated.json
USER node
CMD ["node", "apps/showcase/server.js"]

# Builds one frontend app (APP=web|admin) and serves it with unprivileged nginx.
# Build context: repository root.
ARG APP=web

FROM node:24-alpine AS build
ARG APP
ENV PNPM_HOME=/pnpm PATH=/pnpm:$PATH CI=true
RUN corepack enable
WORKDIR /srv/frontend
COPY frontend/pnpm-lock.yaml frontend/pnpm-workspace.yaml frontend/package.json ./
COPY frontend/apps/web/package.json apps/web/
COPY frontend/apps/admin/package.json apps/admin/
COPY frontend/packages/ui/package.json packages/ui/
COPY frontend/packages/shared/package.json packages/shared/
COPY frontend/packages/api-client/package.json packages/api-client/
RUN --mount=type=cache,id=pnpm,target=/pnpm/store,sharing=locked pnpm install --frozen-lockfile
COPY frontend/ ./
RUN pnpm --filter "@viewallet/${APP}" build

FROM nginxinc/nginx-unprivileged:1.29-alpine AS runtime
ARG APP
COPY infra/docker/nginx/${APP}.conf /etc/nginx/conf.d/default.conf
COPY infra/docker/nginx/security-headers.conf /etc/nginx/snippets/security-headers.conf
COPY --from=build /srv/frontend/apps/${APP}/dist /usr/share/nginx/html
EXPOSE 8080

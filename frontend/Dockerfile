# Uniyra AI Lab — frontend production image (local build, not published).
#
# The browser talks to the backend through the SAME origin (this nginx) at the
# API path /api/, which is reverse-proxied to the backend service.
#
# Build (from the repository root):
#   docker build -t uniyra-ailab-frontend frontend
FROM node:22-alpine AS build

WORKDIR /app
RUN corepack enable

# Reproducible install from the committed lockfile.
COPY package.json yarn.lock ./
RUN yarn install --frozen-lockfile

COPY . .

# CRA inlines this at build time. "/" selects same-origin: the bundle calls the
# relative path /api/, so it works for any hostname, port or https frontend.
ARG REACT_APP_BACKEND_URL=/
ENV REACT_APP_BACKEND_URL=${REACT_APP_BACKEND_URL} \
    GENERATE_SOURCEMAP=false \
    DISABLE_ESLINT_PLUGIN=true

RUN yarn build

FROM nginx:1.27-alpine

COPY --from=build /app/build /usr/share/nginx/html
COPY nginx.conf /etc/nginx/conf.d/default.conf

EXPOSE 80

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=5 \
    CMD wget -q -O /dev/null http://127.0.0.1/ || exit 1

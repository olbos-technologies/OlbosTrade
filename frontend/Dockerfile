# ── Build stage ────────────────────────────────────────────────────────────────
FROM node:20-alpine AS builder

WORKDIR /app

COPY package.json ./
RUN npm install --legacy-peer-deps

COPY . .
# The commit this image is built from, shown in the status bar so a deploy can be
# verified in the browser. update.sh passes it; an unstamped build says "build unknown".
ARG VITE_GIT_SHA=""
ENV VITE_GIT_SHA=$VITE_GIT_SHA
RUN npm run build

# ── Runtime stage ──────────────────────────────────────────────────────────────
FROM nginx:1.25-alpine AS runtime

# apache2-utils provides htpasswd for generating the Basic Auth password file.
RUN apk add --no-cache apache2-utils

# Copy built React app
COPY --from=builder /app/dist /usr/share/nginx/html

# Entrypoint writes the nginx config at startup and enables HTTP Basic Auth over
# the whole app when DASH_USER/DASH_PASS are set (the access control for direct
# IP exposure). SPA routing + /api proxy let it serve behind Caddy AND by IP.
COPY docker-entrypoint.sh /docker-entrypoint-olbos.sh
RUN chmod +x /docker-entrypoint-olbos.sh

EXPOSE 3000

ENTRYPOINT ["/docker-entrypoint-olbos.sh"]

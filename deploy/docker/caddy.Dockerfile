# Kanagatly VMS ingress image: builds the React SPA, then bakes it into Caddy
# together with the Caddyfile so the ingress ships as one self-contained image
# (no bind mount of dist/ needed on the air-gapped host).
#
# Build context is the REPO ROOT (so the Vite build can see web-react/):
#   docker build -f deploy/docker/caddy.Dockerfile -t kanagatly/caddy:latest .

# ── Stage 1: build the SPA ───────────────────────────────────────────────────
FROM node:22-alpine AS web
WORKDIR /web
# Install deps first (cache layer) — only busts when the lockfile changes.
COPY web-react/package.json web-react/package-lock.json ./
RUN npm ci
COPY web-react/ ./
# base is "./" (relative) in vite.config.ts, so the bundle works on any IP/mount.
RUN npm run build

# ── Stage 2: Caddy serving the built SPA ─────────────────────────────────────
FROM caddy:2-alpine
COPY --from=web /web/dist /srv/www
COPY deploy/docker/Caddyfile /etc/caddy/Caddyfile
EXPOSE 8443

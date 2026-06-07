# Chaos Cypher Interface - Web UI
# Multi-stage build for optimized production image

# Stage 1: Build
FROM node:22.22.1-alpine@sha256:8094c002d08262dba12645a3b4a15cd6cd627d30bc782f53229a2ec13ee22a00 AS builder

WORKDIR /app

# Copy package files
COPY packages/interface/package*.json packages/interface/.npmrc ./

# Install dependencies
RUN npm ci

# Copy source code
COPY packages/interface ./

# Build production bundle
RUN npm run build

# Stage 2: Production (non-root nginx)
FROM nginxinc/nginx-unprivileged:1.29.5-alpine@sha256:42a7d7f2ee23e9f5a1dcdf3647ba5c585bbd18f79e79cd817e70e8cd61c55779

# Copy built assets from builder
COPY --from=builder /app/dist /usr/share/nginx/html

# Copy nginx configuration. The main config is templated so Docker can inject
# CHAOSCYPHER_EDGE_AUTH_TOKEN into the protected API proxy rules.
#
# This image consumes static nginx configs; the all-in-one container renders
# equivalents from Pydantic settings via chaoscypher_core.services.orchestration,
# but the interface image doesn't ship the chaoscypher CLI so it keeps the
# static files (kept in sync by hand). A future PR may add a build-time render
# step to eliminate the duplication.
COPY packages/docker/config/multi-interface-nginx.conf /etc/nginx/templates/default.conf.template
COPY packages/docker/config/proxy-common.conf /etc/nginx/proxy-common.conf
COPY packages/docker/config/proxy-public.conf /etc/nginx/proxy-public.conf
COPY packages/docker/config/edge-auth-proxy.conf /etc/nginx/edge-auth-proxy.conf
COPY packages/docker/config/edge-unauthorized.conf /etc/nginx/edge-unauthorized.conf
COPY packages/docker/config/security-headers.conf /etc/nginx/security-headers.conf
COPY packages/docker/multi-container/interface/edge-auth-entrypoint.sh /edge-auth-entrypoint.sh
RUN sed -i 's/\r$//' /edge-auth-entrypoint.sh && chmod +x /edge-auth-entrypoint.sh

# Expose port (nginx-unprivileged listens on 8080 by default)
EXPOSE 8080

# Health check
HEALTHCHECK --interval=30s --timeout=10s --start-period=5s --retries=3 \
    CMD wget --quiet --tries=1 --spider http://localhost:8080/ || exit 1

# Start nginx
ENTRYPOINT ["/edge-auth-entrypoint.sh"]
CMD ["nginx", "-g", "daemon off;"]

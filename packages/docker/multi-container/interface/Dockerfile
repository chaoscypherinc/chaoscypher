# Chaos Cypher Interface - Web UI
# Multi-stage build for optimized production image

# Stage 1: Build
FROM node:22.23.4-alpine@sha256:fe4f6d74e20370acb04ad889077d14d0f64a9c8ea6a42bc60cddf1e844699914 AS builder

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
FROM nginxinc/nginx-unprivileged:1.31.7-alpine@sha256:b5047b9b76845eac124358373c6852377c52e4b05834a47647e955dd91cb8f1e

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

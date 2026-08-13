FROM node:22-bookworm-slim AS builder

WORKDIR /build
COPY package.json package-lock.json ./
RUN npm ci
COPY . .
RUN npm run build

FROM node:22-bookworm-slim AS product-builder

WORKDIR /product
COPY product-web/package.json product-web/package-lock.json ./
RUN npm ci
COPY product-web/ ./
RUN npm run build

FROM node:22-bookworm-slim

ENV NODE_ENV=production
WORKDIR /app
COPY --from=builder --chown=node:node /build/dist ./dist
COPY --from=builder --chown=node:node /build/web-server.mjs ./web-server.mjs
COPY --from=product-builder --chown=node:node /product/dist ./product-dist

USER node
EXPOSE 3100
CMD ["node", "web-server.mjs"]

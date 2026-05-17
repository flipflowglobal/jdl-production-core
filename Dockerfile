FROM rust:1.77-alpine AS rust-build
WORKDIR /app
COPY rust-core/ rust-core/
RUN cd rust-core && cargo build --release

FROM node:20-alpine AS node-build
WORKDIR /app
COPY package*.json ./
RUN npm ci
COPY tsconfig.json ./
COPY src/ src/
RUN npx tsc

FROM python:3.12-slim
WORKDIR /app
RUN apt-get update && apt-get install -y --no-install-recommends nodejs && rm -rf /var/lib/apt/lists/*
COPY --from=rust-build /app/rust-core/target/release/jdl-core /usr/local/bin/jdl-core
COPY --from=node-build /app/dist/ dist/
COPY --from=node-build /app/node_modules/ node_modules/
COPY package.json ./
COPY python/ python/
ENV NODE_ENV=production
EXPOSE 8420
CMD ["node", "dist/index.js"]

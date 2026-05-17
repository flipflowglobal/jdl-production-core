FROM node:20-alpine AS build
WORKDIR /app
COPY package*.json ./
RUN npm ci
COPY tsconfig.json ./
COPY src/ src/
RUN npx tsc

FROM node:20-alpine
WORKDIR /app
RUN apk add --no-cache python3
COPY --from=build /app/dist dist/
COPY --from=build /app/node_modules node_modules/
COPY --from=build /app/package.json ./
COPY python/ python/
COPY src/services/schema.sql dist/services/schema.sql
ENV NODE_ENV=production

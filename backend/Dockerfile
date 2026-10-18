FROM node:26-bookworm-slim AS build
WORKDIR /app
RUN npm install -g npm@12.0.2
COPY package*.json ./
RUN npm ci --omit=dev
COPY src ./src
FROM node:26-bookworm-slim AS runtime
WORKDIR /app
RUN apt-get update \
    && apt-get upgrade -y \
    && rm -rf /var/lib/apt/lists/*
RUN rm -rf /usr/local/lib/node_modules/npm \
    /usr/local/bin/npm \
    /usr/local/bin/npx
COPY --from=build /app/node_modules ./node_modules
COPY --from=build /app/src ./src
USER node
EXPOSE 3000
CMD ["node", "src/server.js"]

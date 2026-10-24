# Local SpacetimeDB server with the game module published on start.
FROM node:24-bookworm-slim

RUN apt-get update && apt-get install -y --no-install-recommends curl ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# Pinned so everyone on the team runs the same SpacetimeDB.
ENV SPACETIME_DOWNLOAD_ROOT=https://github.com/clockworklabs/SpacetimeDB/releases/download/v2.13.0
RUN curl -sSf https://install.spacetimedb.com | sh -s -- --yes
ENV PATH="/root/.local/bin:${PATH}"

WORKDIR /game
COPY game/spacetimedb/package.json game/spacetimedb/package-lock.json spacetimedb/
RUN cd spacetimedb && npm ci --no-audit --no-fund
COPY game/spacetimedb spacetimedb
COPY docker/spacetime-entrypoint.sh /entrypoint.sh

EXPOSE 3000
ENTRYPOINT ["sh", "/entrypoint.sh"]

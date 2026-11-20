# tb-gateway: FastMCP gateway + the Camoufox browser tools.
#
# EVERYTHING HERE IS FROZEN. A rebuild must produce the same working gateway as
# the last one, months later, with nobody having changed anything. Nothing is
# allowed to fetch "the newest" version at build time or at run time:
#   - base image pinned by digest
#   - Node: the nodejs.org build of one version, checked by sha256; pnpm the same way
#   - Python deps installed from requirements.lock (exact versions, all transitive,
#     each file checked by sha256)
#   - the browser tool server installed from camoufox-mcp/pnpm-lock.yaml
#   - the Camoufox browser itself: one exact release, checked by sha256
# Updating any of these is a deliberate act: see docs/operations.md.
FROM python:3.12.15-slim-trixie@sha256:a0f57064cbdcd6285fa45f51237ab0b35ea8ebaed06a1f5973e89e19dfb86704

# yt-dlp -> ffmpeg. Camoufox is a real Firefox build -> GUI libs + Xvfb.
# The browser tool server's native deps (impit, better-sqlite3) -> build-essential + python3.
RUN apt-get update && apt-get install -y --no-install-recommends \
        ca-certificates \
        ffmpeg \
        curl \
        build-essential python3 \
        libgtk-3-0 libdbus-glib-1-2 libasound2 \
        libx11-xcb1 libxcomposite1 libxdamage1 libxrandr2 libxtst6 \
        libpangocairo-1.0-0 libcairo2 libfontconfig1 libfreetype6 \
        libxkbcommon0 libxshmfence1 libnss3 libatk1.0-0 libatk-bridge2.0-0 \
        xvfb \
    && rm -rf /var/lib/apt/lists/*

# Node from nodejs.org, checked against the sha256 nodejs.org publishes for this file.
# (It is byte for byte the node binary the NodeSource package of this version had.)
ARG NODE_VERSION=22.23.3
ARG NODE_SHA256=1084aa36196bba4c3a5e69a1ee388a6e4ff729dad09445fbcd434b28fe3c24af
RUN curl -fsSL -o /tmp/node.tar.gz "https://nodejs.org/dist/v${NODE_VERSION}/node-v${NODE_VERSION}-linux-x64.tar.gz" \
    && echo "${NODE_SHA256}  /tmp/node.tar.gz" | sha256sum -c - \
    && tar -xzf /tmp/node.tar.gz -C /usr/local --strip-components=1 --no-same-owner \
        "node-v${NODE_VERSION}-linux-x64/bin" "node-v${NODE_VERSION}-linux-x64/lib" \
        "node-v${NODE_VERSION}-linux-x64/include" "node-v${NODE_VERSION}-linux-x64/share" \
    && rm /tmp/node.tar.gz \
    && node --version

WORKDIR /app

COPY requirements.lock .
RUN pip install --no-cache-dir --require-hashes -r requirements.lock

# Browser tool server (camoufox-mcp-server 2.0.7 + camoufox-js 0.10.2 + playwright-core 1.51.1),
# installed from the lockfile so no transitive dependency can float.
# pnpm 9, not 10: pnpm 10 skips install scripts, which breaks better-sqlite3's native build.
# .npmrc keeps auto-install-peers=false, matching the lockfile.
# The registry's own integrity value for pnpm 9.15.9, as sha512 hex.
ARG PNPM_SHA512=68046141893c66fad01c079231128e9afb89ef87e2691d69e4d40eee228988295fd4682181bae55b58418c3a253bde65a505ec7c5f9403ece5cc3cd37dcf2531
RUN curl -fsSL -o /tmp/pnpm.tgz https://registry.npmjs.org/pnpm/-/pnpm-9.15.9.tgz \
    && echo "${PNPM_SHA512}  /tmp/pnpm.tgz" | sha512sum -c - \
    && npm install -g /tmp/pnpm.tgz \
    && rm /tmp/pnpm.tgz
WORKDIR /opt/camoufox-mcp
COPY camoufox-mcp/package.json camoufox-mcp/pnpm-lock.yaml camoufox-mcp/.npmrc ./
RUN pnpm install --frozen-lockfile --prod
COPY camoufox-mcp/patch-camoufox-js.sh camoufox-mcp/patch-dns-sinkhole.py camoufox-mcp/install-browser.sh camoufox-mcp/selftest.mjs ./
RUN ./patch-camoufox-js.sh && python3 patch-dns-sinkhole.py

# The Camoufox browser, frozen at one exact release of github.com/daijro/camoufox.
# install-browser.sh takes the release zip from gateway/vendor/ if it is there (offline
# builds), otherwise downloads it, and stops the build unless its sha256 matches.
# It also installs uBlock Origin (pinned the same way) and the GeoIP database, which
# camoufox-js would otherwise download when the browser first starts.
# (Do not set PLAYWRIGHT_SKIP_BROWSER_DOWNLOAD: in camoufox-js 0.10.2 it also stops
# the installed uBlock Origin from being loaded.)
ARG CAMOUFOX_VERSION=156.0.1
ARG CAMOUFOX_RELEASE=beta.33
ARG CAMOUFOX_SHA256=730d731153b2a16cac238a0b4a7f849c04d1fd6181c09ec6af29695af0407af8
ARG UBO_URL=https://addons.mozilla.org/firefox/downloads/file/5034826/ublock_origin-1.75.0.xpi
ARG UBO_SHA256=5b74415860456370644bd80f16125e865b0e6c356bb5dfcfb84069967eaa5287
RUN --mount=type=bind,source=vendor,target=/vendor ./install-browser.sh

# Prove driver + protocol library + browser work together before this image can replace
# the running one. Runs from inside the server package so it resolves the same camoufox-js.
RUN d=$(readlink -f node_modules/camoufox-mcp-server) \
    && cp selftest.mjs "$d/selftest.mjs" \
    && node "$d/selftest.mjs" \
    && rm "$d/selftest.mjs"

WORKDIR /app
COPY server.py watch.py demo.py ./
COPY fixtures/ /app/fixtures/

EXPOSE 8000

CMD ["python", "server.py"]

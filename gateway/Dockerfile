FROM python:3.12-slim

# yt-dlp → ffmpeg. https → ca-certificates. NodeSource → curl + gnupg.
# Camoufox runs a real Firefox binary even in headless mode — needs GUI libs + Xvfb.
# camoufox-mcp pulls native deps (impit, better-sqlite3) that need build-essential + python3
# to compile via node-gyp when prebuilt binaries aren't available.
RUN apt-get update && apt-get install -y --no-install-recommends \
        ca-certificates \
        ffmpeg \
        curl gnupg \
        build-essential python3 \
        libgtk-3-0 libdbus-glib-1-2 libasound2 \
        libx11-xcb1 libxcomposite1 libxdamage1 libxrandr2 libxtst6 \
        libpangocairo-1.0-0 libcairo2 libfontconfig1 libfreetype6 \
        libxkbcommon0 libxshmfence1 libnss3 libatk1.0-0 libatk-bridge2.0-0 \
        xvfb \
    && curl -fsSL https://deb.nodesource.com/setup_22.x | bash - \
    && apt-get install -y --no-install-recommends nodejs \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# camoufox-mcp-server pulls a native dep (`impit`) whose preinstall enforces pnpm via
# `npx only-allow pnpm`. Plain `npm install -g` fails the check; use pnpm.
# pnpm refuses to install if its global bin dir isn't in PATH — point it at /usr/local/bin
# (which is) instead of pnpm's default $PNPM_HOME/bin.
# Note: Camoufox's Firefox binary (~300MB) is downloaded on FIRST tool call, not at build time.
# Pin pnpm 9 — pnpm 10 blocks lifecycle scripts by default which breaks the
# better-sqlite3 native build (prebuild downloads + node-gyp fallbacks rely on
# postinstall scripts). pnpm 9 runs them and the global install picks up the
# native .node binary cleanly.
# Pin camoufox-mcp-server to a known-working 2.x to avoid surprise upstream churn.
RUN npm install -g pnpm@9 && \
    pnpm config set global-bin-dir /usr/local/bin && \
    pnpm add -g camoufox-mcp-server@2.0.7

COPY server.py .

EXPOSE 8000

CMD ["python", "server.py"]

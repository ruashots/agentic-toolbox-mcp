# Agentic Toolbox MCP

**Give your AI agent the tools to research, browse, read, transcribe, and speak.**

[![CI](https://github.com/ruashots/agentic-toolbox-mcp/actions/workflows/ci.yml/badge.svg)](https://github.com/ruashots/agentic-toolbox-mcp/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
[![Tools: 27](https://img.shields.io/badge/MCP_tools-27-06b6d4)](#whats-in-the-box)

One self-hosted MCP endpoint. **27 tools. No API keys required.** Run it with Docker Compose, connect your agent, and put web search, two browser engines, document conversion, video tools, local transcription, and speech generation within reach.

Host it on a workstation or home server and share it across your MCP clients. Speech and document processing run on your hardware; web tools reach the sites you ask for.

[Get started](#quick-start) · [Tools](#whats-in-the-box) · [Agent guide](AGENT_HANDOFF.md) · [Operations](docs/operations.md)

![Recorded demo showing web search, page reading, browser navigation, and video metadata through the toolbox](docs/media/demo.svg)

*Actual tool output captured by the included [demo script](gateway/demo.py). Run it against your own installation with `docker exec tb-gateway python demo.py`.*

## What will you build with it?

Give a connected agent a task like:

> “Research this topic, read the strongest sources, and write a brief with links.”

> “Summarize this video. Use its captions if available; otherwise transcribe the audio.”

> “Read this PDF and pull out the pricing table.”

> “Open my dashboard, navigate to the report, and take a screenshot.”

> “Turn this script into a narrated WAV file.”

The toolbox supplies the tools; your agent chooses and combines them. The [agent guide](AGENT_HANDOFF.md) includes selection rules, fallbacks, and recipes you can add to its context.

## Why run this toolbox?

- **One connection for the whole stack.** Search, browsing, files, video, and speech appear through a single MCP endpoint.
- **Two ways to read the web.** Crawl4AI renders pages into Markdown. Camoufox adds a stealth Firefox browser, screenshots, and stateful click-and-fill workflows.
- **Local speech without a GPU requirement.** Whisper handles transcription; OmniVoice generates speech with optional voice cloning. AMD GPU acceleration is available for transcription.
- **Health checks that do real work.** A watcher searches, loads pages, converts fixtures, and tests audio. Tool errors include health context and suggested alternatives, helping agents avoid pointless retries.
- **Deliberate privacy defaults.** Agent request logs are disabled, temporary files live in RAM-backed storage, and browser sessions expire. See the [privacy details](#security-and-privacy) for the boundaries.
- **Pinned builds and browser checks.** Container images, dependencies, and model revisions are pinned. The gateway build tests browser startup and network-access rules.

## Quick start

### 1. Start the stack

You need an **x86-64 machine**, Docker with **Compose v2**, at least **8 GB RAM**, and **55 GB free disk**. No GPU or API keys are required. This is a seven-container stack: plan for an initial download of roughly 5.5 GB and about 37–50 GB of images, build cache, and models afterward, depending on Docker's storage setup.

```bash
git clone https://github.com/ruashots/agentic-toolbox-mcp.git
cd agentic-toolbox-mcp
docker compose up -d --build
```

**Use a trusted network.** The default configuration publishes the MCP endpoint on port **8000** and the Stirling-PDF UI on **8085**, both without authentication. Keep them behind your firewall; see [security and privacy](#security-and-privacy) before allowing remote access.

Check startup progress after the build:

```bash
docker logs --tail 30 tb-watch
```

Look for checks such as `[watch] ping: ok` and `[watch] search_web: ok`. Speech models download on first use, including the watcher's first speech checks. Speech synthesis may initially show as down while its roughly 3 GB model downloads, then recover automatically.

### 2. Connect your agent

Add a server to your MCP client with these settings:

| Setting | Value |
|---|---|
| Name | `toolbox` |
| Transport | Streamable HTTP |
| URL, on the same machine | `http://localhost:8000/mcp` |
| URL, from another machine | `http://<server-address>:8000/mcp` |
| Authentication | None |

Reconnect or start a new agent session so it discovers the tools. Ask it to call `ping` (expected result: `pong`), then `toolbox_status`.

For setup guidance and tool usage recipes, give your agent [AGENT_HANDOFF.md](AGENT_HANDOFF.md).

### 3. See it work

```bash
docker exec tb-gateway python demo.py
```

The demo calls the running toolbox to search the web, read a page, navigate with Camoufox, and fetch video metadata. Then try one of the prompts above with your agent.

## What's in the box?

Ten gateway tools plus seventeen `cf_*` browser tools:

| Capability | Tools | Powered by |
|---|---|---|
| Web search | `search_web` | SearXNG |
| Rendered pages → Markdown | `fetch_page` | Crawl4AI / Chromium |
| Stealth browsing, screenshots, links, forms, and multi-step sessions | `cf_browse`, `cf_browse_screenshot`, `cf_browse_session_*`, and more | Camoufox / Firefox |
| Word, PowerPoint, Excel, HTML, CSV, EPUB → Markdown | `convert_document` | MarkItDown |
| PDF text extraction | `pdf_extract_text` | Stirling-PDF |
| Video metadata and available captions | `extract_video` | yt-dlp |
| Audio and video downloads | `download_media` | yt-dlp |
| Audio/video → text with timestamps | `transcribe_audio` | whisper.cpp |
| Text → WAV, optional voice cloning | `synthesize_speech` | OmniVoice |
| Connectivity and tool health | `ping`, `toolbox_status` | Gateway + watcher |

### Useful defaults and limits

- **Pages:** try `fetch_page` first, then `cf_browse` for difficult sites. Stealth browsing does not guarantee access to every protected page.
- **Browser sessions:** one active session holds the browser. Close it when finished; idle sessions expire after 10 minutes.
- **PDFs:** prefer `pdf_extract_text` for reading order and table rows. `convert_document` does not provide OCR for scans or images.
- **Videos:** try captions with `extract_video` before paying the compute cost of transcription. Downloads return base64 and default to a 25 MB limit.
- **Transcription:** defaults to `base.en`. For language detection, choose a multilingual model and pass `language="auto"`. Models are downloaded on demand and cached.
- **Speech:** CPU synthesis is slower than real time; budget roughly 3–5 times the audio length at default quality, depending on hardware. It suits narration and batch work. Supply `reference_text` alongside a voice reference to avoid an extra transcription step.
- **File paths:** a local path refers to the gateway container's filesystem, not your MCP client's machine. Use a reachable URL when the file is elsewhere.

## How it fits together

```text
Your MCP clients
       │
       │ Streamable HTTP · :8000/mcp
       ▼
  MCP gateway ──┬── SearXNG          web search
               ├── Crawl4AI         rendered page reading
               ├── Camoufox         browser automation¹
               ├── MarkItDown       document conversion¹
               ├── yt-dlp           video and audio tools¹
               ├── Stirling-PDF     PDF extraction
               ├── whisper.cpp      transcription
               └── OmniVoice        speech generation

  Watcher ── calls tools through MCP ── reports health to gateway

  ¹ Runs inside the gateway container.
```

The browser MCP server is mounted through FastMCP, so its tools appear alongside the gateway's own tools. Containers restart with Docker unless explicitly stopped. See [operations](docs/operations.md) for architecture, health-check schedules, upgrades, and troubleshooting.

## Transcription on an AMD GPU

The optional overlay gives whisper.cpp access to an AMD GPU through Vulkan. Speech synthesis remains CPU-only.

Check your device group IDs:

```bash
stat -c '%g %n' /dev/dri/*
```

Copy `.env.example` to `.env`, uncomment `COMPOSE_FILE`, and set `VIDEO_GID` and `RENDER_GID` to match your host. Then run:

```bash
docker compose up -d
```

See [the GPU overlay](docker-compose.amd-gpu.yml) for the configuration.

## Security and privacy

**This is a toolbox for trusted agents on a trusted network.** It has no built-in login, and its tools can access network resources. Do not expose ports 8000 or 8085 directly to the internet. For remote use, restrict access through a private network or an authenticated proxy. The PDF UI port is optional; removing its port mapping does not affect MCP tools.

Agent request/response logging is disabled by default. Temporary downloads, documents, audio, and browser profiles use RAM-backed storage. The watcher retains only results from its own fixed test jobs. Model caches and health state persist in Docker volumes.

RAM-backed storage can still reach disk through host swap; use disabled or encrypted swap if that matters for your deployment. Web searches and fetches contact external services, which may retain their own logs. Model downloads, scheduled health checks, and browser startup also make external requests. This is not an offline stack.

The Camoufox guard allows explicitly requested LAN destinations while blocking internet pages from reaching the LAN. Other tools do not provide the same address restrictions. Read the [full privacy and network details](docs/operations.md#privacy-details) before deploying on a sensitive network.

## Contribute

Useful contributions include reproducible bug reports, better tool descriptions, browser compatibility fixes, and new capabilities with real-job health checks. For a bug, include the failing tool, a minimal input, and relevant `toolbox_status` or watcher output; remove private data first.

The [operations guide](docs/operations.md#adding-a-tool) explains how to add a tool. CI runs Python linting and tests, shell checks, Compose validation, and a gateway image build with browser self-tests.

## License

This repository is [MIT licensed](LICENSE). Bundled services, libraries, and downloaded models retain their own licenses.

**If this is the kind of infrastructure you want for your agents, give the repo a star.** It helps other builders find it. If you build something with it, share your workflow in an issue.

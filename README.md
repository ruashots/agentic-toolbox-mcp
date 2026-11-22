# Agentic Toolbox MCP

**One MCP server for all your agents: search, browse, read, transcribe, and speak.**

[![CI](https://github.com/ruashots/agentic-toolbox-mcp/actions/workflows/ci.yml/badge.svg)](https://github.com/ruashots/agentic-toolbox-mcp/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
[![Tools: 27](https://img.shields.io/badge/MCP_tools-27-06b6d4)](#whats-in-the-box)

[![Animated overview: four agents connect to one MCP toolbox for search, browsing, reading, transcription, and speech; a custom tool is added and becomes available to each agent](docs/media/toolbox-hero.gif)](docs/media/toolbox-hero.mp4)

[Video with music and sound effects (27 seconds, MP4)](docs/media/toolbox-hero.mp4)

*Concept animation. Compatible clients share one endpoint and service capacity; an active Camoufox session blocks other Camoufox calls. To make an added tool available to your agents, rebuild and restart the affected services, then reconnect the clients. See [limits](#useful-defaults-and-limits) and [extension steps](#extend-the-toolbox).*

One self-hosted Model Context Protocol (MCP) endpoint. **27 tools. No API keys required by the bundled toolbox.** Run one Docker Compose stack and connect your compatible agent clients to the same URL for web search, two browser engines, document conversion, video tools, local transcription, and speech generation.

Built for a shared tool layer across local-LLM agents, coding assistants, and custom agent applications. It is especially useful when an agent's current setup has no web search or you want a self-hosted search service shared across clients. The toolbox supplies tools, not the agent's language model or runtime; those remain your choice and may have their own credentials and costs.

Host it on a workstation or home server and share it across your MCP clients. Speech and document processing run on the toolbox host; web search and browsing make external requests.

[Get started](#quick-start) · [Tools](#whats-in-the-box) · [Extend it](#extend-the-toolbox) · [Agent guide](AGENT_HANDOFF.md) · [Operations](docs/operations.md)

## Which agents can use it?

The connection requirement is an **MCP client with Streamable HTTP support** that can reach the toolbox host and expose its tools to an agent. Compatibility depends on the client and its configuration, not just the model name.

- **Local-LLM agents:** connect through an MCP-capable application or your own client integration. That application must let the model select tools, execute the calls, and return results to the model. Running a local model alone does not provide that integration. The Compose stack does not include an agent LLM server.
- **Coding assistants:** [Codex supports Streamable HTTP MCP servers](https://learn.chatgpt.com/docs/extend/mcp), and [Claude Code supports HTTP MCP servers](https://code.claude.com/docs/en/mcp). Both can connect to this endpoint through their MCP configuration.
- **Custom agents and other MCP clients:** use the connection settings below. Clients that only support stdio need a separate adapter or HTTP integration; this Compose stack exposes Streamable HTTP.

This toolbox is especially useful for local-LLM agents and Codex or Claude Code setups without usable built-in search. [Codex web search](https://learn.chatgpt.com/docs/web-search) is a hosted OpenAI tool: custom providers default to `supports_standalone_web_search = false`, and standalone search is still under development and disabled by default. [Claude Code's WebSearch](https://code.claude.com/docs/en/tools-reference#websearch-tool-behavior) is a server-side tool available through the Claude API, Claude Platform on AWS, Claude Models on Google Cloud (Anthropic managed), and Microsoft Foundry; Amazon Bedrock does not expose it. Its search backend cannot be configured, and the documentation directs users to an MCP search server for a different provider. Here, `search_web` supplies SearXNG-backed search through MCP, giving compatible clients a shared search service when their setup lacks search or they want a self-hosted alternative.

## What will your agents build with it?

Give a connected agent a task like:

> “Research this topic, read the strongest sources, and write a brief with links.”

> “Summarize this video. Use its captions if available; otherwise transcribe the audio.”

> “Read this PDF and pull out the pricing table.”

> “Open my dashboard, navigate to the report, and take a screenshot.”

> “Turn this script into a narrated WAV file.”

The toolbox supplies the tools; each agent chooses and combines them through its client. The [agent guide](AGENT_HANDOFF.md) includes selection rules, fallbacks, and recipes you can add to your agents' context.

## Why run this toolbox?

- **One shared endpoint for your agents.** Configure each compatible client to use the same toolbox URL. Search, browsing, files, video, and speech appear through that endpoint.
- **Room for your own tools.** Add Python functions, connect another service, or mount an existing MCP server behind the same endpoint. Connected agents discover the additions when they reconnect.
- **Two ways to read the web.** Crawl4AI renders pages into Markdown. Camoufox adds a stealth Firefox browser, screenshots, and stateful click-and-fill workflows.
- **Local speech without a GPU requirement.** Whisper handles transcription; OmniVoice generates speech with optional voice cloning. AMD GPU acceleration is available for transcription.
- **Health checks that do real work.** A watcher searches, loads pages, converts fixtures, and tests audio. Tool errors include health context and suggested alternatives, helping agents avoid pointless retries.
- **Deliberate privacy defaults.** Agent request logs are disabled, temporary files live in RAM-backed storage, and browser sessions expire. See the [privacy details](#security-and-privacy) for the boundaries.
- **Version pins and browser checks.** Base and service images use digests, Python and Node dependencies use lockfiles, and speech model downloads use pinned revisions. OS packages installed with `apt-get` are not individually pinned. The gateway build tests browser startup and network-access rules.

## Quick start

### 1. Start the stack

Use an **x86-64 machine** with Docker and **Compose v2**. Budget at least **8 GB RAM** and **55 GB free disk** for the toolbox, with additional capacity for any local agent models and concurrent work. No GPU or API keys are required by the bundled services. This is a seven-container stack; downloads, images, build cache, and model caches consume additional space as you build and use it.

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

### 2. Connect your agents

Add a server to each MCP client with these settings:

| Setting | Value |
|---|---|
| Name | `toolbox` |
| Transport | Streamable HTTP |
| URL, on the same machine | `http://localhost:8000/mcp` |
| URL, from another machine | `http://<server-address>:8000/mcp` |
| Authentication | None |

For a local-LLM application or custom agent, configure these settings in the component that manages MCP connections and tool execution. The URL must be reachable from that component: `localhost` refers to the client's own environment, which may be a container or remote host.

For **Codex CLI**, add the endpoint with:

```bash
codex mcp add toolbox --url http://localhost:8000/mcp
```

For **Claude Code**, add it for your user across projects with:

```bash
claude mcp add --transport http --scope user toolbox http://localhost:8000/mcp
```

Replace `localhost` with the toolbox host's address when connecting from elsewhere. See the official [Codex MCP configuration](https://learn.chatgpt.com/docs/extend/mcp) and [Claude Code MCP configuration](https://code.claude.com/docs/en/mcp) for client settings and policy controls.

Reconnect or start a new agent session so the client discovers the tools. Ask each agent to call `ping` (expected result: `pong`), then `toolbox_status`. Tool names may have a client-added prefix. For long transcription or synthesis jobs, increase the client's tool-call timeout as needed.

The gateway rejects requests with an unapproved `Origin` header by default. Clients that send one need their trusted origin explicitly configured in the gateway's `FASTMCP_HTTP_ALLOWED_ORIGINS` setting, a JSON list. See [`http_guard()`](gateway/server.py) for the implementation.

For tool usage recipes, give your agents [AGENT_HANDOFF.md](AGENT_HANDOFF.md). Its Claude Code setup examples are client-specific; the tool guidance also applies to other connected agents.

### 3. See it work

![Recorded demo showing web search, page reading, browser navigation, and video metadata through the toolbox](docs/media/demo.svg)

*Actual tool output captured by the included [demo script](gateway/demo.py). Run it against your own installation:*

```bash
docker exec tb-gateway python demo.py
```

The demo calls the running toolbox to search the web, read a page, navigate with Camoufox, and fetch video metadata. Then try one of the prompts above with your agents.

## What's in the box?

Ten gateway tools plus seventeen `cf_*` browser tools:

| Capability | Tools | Powered by |
|---|---|---|
| Web search | `search_web` | SearXNG |
| Rendered pages → Markdown | `fetch_page` | Crawl4AI / Chromium |
| Stealth browsing, screenshots, links, forms, and multi-step sessions | `cf_browse`, `cf_browse_screenshot`, `cf_browse_session_*`, and more | Camoufox / Firefox |
| Word, PowerPoint, Excel, HTML, CSV, EPUB → Markdown | `convert_document` | MarkItDown |
| PDF text extraction | `pdf_extract_text` | Stirling-PDF |
| Video metadata and available English auto-captions | `extract_video` | yt-dlp |
| Audio and video downloads | `download_media` | yt-dlp |
| Audio/video → text with timestamps | `transcribe_audio` | whisper.cpp |
| Text → WAV, optional voice cloning | `synthesize_speech` | OmniVoice |
| Connectivity and tool health | `ping`, `toolbox_status` | Gateway + watcher |

### Useful defaults and limits

- **Pages:** try `fetch_page` first, then `cf_browse` for difficult sites. Stealth browsing does not guarantee access to every protected page.
- **Shared browser:** all clients share the Camoufox browser. One active session holds it and blocks other Camoufox calls until closed or expired. Close sessions when finished; idle sessions expire after 10 minutes. Sharing the endpoint does not provide a separate browser per agent.
- **PDFs:** prefer `pdf_extract_text` for reading order and table rows. `convert_document` does not provide OCR for scans or images.
- **Videos:** try captions with `extract_video` before paying the compute cost of transcription. Downloads return base64 and default to a 25 MB limit.
- **Transcription:** defaults to `base.en`. For language detection, choose a multilingual model and pass `language="auto"`. Models are downloaded on demand and cached.
- **Speech:** synthesis runs on CPU and can take longer than the audio's duration. Latency depends on hardware, text length, and quality settings. Supply `reference_text` alongside a voice reference to avoid an extra transcription step.
- **File paths:** a local path refers to the gateway container's filesystem, not your MCP client's machine. Use a reachable URL when the file is elsewhere.

## How it fits together

```text
Local-LLM agents / coding assistants / custom agents
       │ MCP-capable clients
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

The browser MCP server is mounted through FastMCP, so its tools appear alongside the gateway's own tools. Clients share the backing services and their capacity. Containers use Docker's `unless-stopped` restart policy. See [operations](docs/operations.md) for architecture, health-check schedules, upgrades, and troubleshooting.

## Extend the toolbox

The 27 bundled tools are a starting point. You can extend your installation with capabilities your agents need while keeping one MCP connection per client. There are three main paths:

- **Write a tool in Python.** Add an `@mcp.tool` function in [`gateway/server.py`](gateway/server.py), with a clear docstring describing when to use it. Pin any new Python dependencies in [`gateway/requirements.lock`](gateway/requirements.lock).
- **Connect a backing service.** Add its container to [`docker-compose.yml`](docker-compose.yml) on the `toolbox` network, inherit the `*private` logging defaults, and expose the capability through a gateway tool. The existing search and speech services demonstrate this pattern.
- **Reuse an existing MCP server.** Mount it through FastMCP's `create_proxy()` and `mcp.mount()`, following the Camoufox integration in the gateway. Its tools can then appear alongside the toolbox's own tools.

For new capabilities, add a real-job check and description in [`gateway/watch.py`](gateway/watch.py), plus fallback guidance in the gateway's `_ALTERNATIVES`. This lets agents see health information and useful alternatives when a tool fails.

Extensions require changes to the installation and a rebuild of the affected images, including the watcher when its checks change. After restarting the services, reconnect your clients to discover the new tools at the same `/mcp` URL. See [Adding a tool](docs/operations.md#adding-a-tool) for the implementation and rebuild steps.

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

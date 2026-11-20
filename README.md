# Agentic Toolbox

**Give your agents the tools to work across the web and your homelab.**

Agentic Toolbox exists to empower agents. Its capabilities are the product: web search, browser automation, document reading, video tools, transcription, and speech generation behind one MCP endpoint. Run it on your own machine or home server so your agents can interact with services, applications, websites, and local infrastructure.

Read a public website, work through a local dashboard, extract a PDF from your NAS, or transcribe a recording served on your LAN. Local-network access is a first-class feature. Privacy-first means no retention of agent requests, browsing history, URLs, payloads, or responses, and no unauthorized disclosure. See [how the defaults implement that](#security-and-privacy).

No API keys to configure. No GPU required. Start the stack with Docker Compose.

[Quick start](#quick-start) · [Tools](#what-your-agent-can-do) · [Agent guide](AGENT_HANDOFF.md) · [Operations](docs/operations.md)

## What your agent can do

| Give it a task | Tools it can use | Powered by |
|---|---|---|
| Research a topic and read the sources | `search_web` returns titles, links, and snippets; `fetch_page` reads JavaScript-rendered pages as markdown | SearXNG + Crawl4AI |
| Use websites and homelab dashboards: browse, click, fill forms, and take screenshots | `cf_browse`, `cf_browse_session_*`, `cf_browse_screenshot`, and other `cf_*` tools | Camoufox |
| Read documents and spreadsheets | `convert_document` turns Word, PowerPoint, Excel, HTML, CSV, and EPUB into markdown | MarkItDown |
| Extract text from a PDF | `pdf_extract_text` returns plain text in reading order | Stirling-PDF |
| Work with video and audio from the web or your LAN | `extract_video` retrieves metadata and available English auto-captions; `download_media` returns the media file as base64 | yt-dlp |
| Transcribe a recording | `transcribe_audio` returns text and timestamped segments | Local Whisper via whisper.cpp |
| Create narration, including with a reference voice | `synthesize_speech` returns WAV audio as base64, with optional voice cloning | Local OmniVoice |
| Check what is working | `ping` checks connectivity; `toolbox_status` reports scheduled tool checks | Built-in watcher |

**See it in action.** This is real output from the included [demo script](gateway/demo.py), captured against a fresh install. Results and timings vary by machine and site.

![Agentic Toolbox demo showing web search, page reading, browser navigation, video metadata, and tool health checks](docs/media/demo.svg)

## Built for agents that do things

- **Your homelab is within reach.** Agents can open LAN pages, use local web applications, and read documents and media served by your infrastructure. The browser accepts local-network addresses supplied by the agent.
- **One connection for the whole stack.** The gateway exposes the tools through MCP Streamable HTTP, so agents on your trusted network can share the same installation.
- **Two ways to read the web.** Start with rendered markdown from Crawl4AI. Use Camoufox for browser interactions or as a fallback on sites with bot protection. Access still depends on the site.
- **Speech runs on your hardware.** Transcription and synthesis use local models, downloaded when first needed and cached across container restarts.
- **Health checks run real jobs.** The watcher searches, loads pages, converts fixtures, transcribes audio, and generates speech. One browser probe covers the shared `cf_*` browser. Failed tool calls include health context and fallback suggestions where available.
- **Pinned components and browser checks.** Images, application dependencies, and model revisions are pinned. The gateway build launches the bundled browser and renders a page to check that it works with its driver.

## Quick start

### 1. Prepare your host

Use **x86-64 Linux**, or **Windows through WSL 2**, with Git, Docker, and **Docker Compose v2**. ARM and Apple Silicon Macs are not supported.

Plan for **8 GB of RAM and 55 GB of free disk**, as budgeted in the [installation guide](AGENT_HANDOFF.md#1-nothing-is-set-up). The first build downloads dependencies and the browser; speech models download on first use. The default stack runs on CPU.

**Use a trusted network.** The MCP endpoint has no login. Compose publishes port **8000** for MCP and **8085** for the Stirling-PDF web UI, also without login. Keep both off the public internet.

### 2. Start the toolbox

```bash
git clone https://github.com/Ruashots/agentic-toolbox-mcp.git
cd agentic-toolbox-mcp
docker compose up -d --build
```

No `.env` file is needed for the default setup. This starts seven containers, including the gateway and watcher.

After the build finishes, check progress:

```bash
docker logs --tail 30 tb-watch
```

Look for `[watch] ping: ok` and `[watch] search_web: ok`. The initial speech checks also trigger model downloads, so those checks can take longer to pass. The watcher retries failures automatically.

### 3. Connect your agent

For **Claude Code** on the same machine:

```bash
claude mcp add --transport http --scope user toolbox http://localhost:8000/mcp
```

Start a new Claude Code session to load the tools. Use `/mcp` to check the connection.

For another MCP client, add a server with these settings:

| Setting | Value |
|---|---|
| Name | `toolbox` |
| Transport | Streamable HTTP |
| URL | `http://localhost:8000/mcp` |
| Authentication | None |

If the toolbox runs on another machine, replace `localhost` with that machine's reachable hostname or IP address. Your client must support MCP Streamable HTTP.

Ask your agent to call `ping`. It should return `pong`. Then try:

> Use the toolbox to search for Model Context Protocol, read the top sources, and summarize what they say with links.

For tool selection, browser sessions, transcription models, and workflow recipes, give your agent [AGENT_HANDOFF.md](AGENT_HANDOFF.md).

### 4. Run the demo

You can also exercise the tools directly from your shell:

```bash
docker exec tb-gateway python demo.py
```

It calls search, page reading, the browser, video metadata, and status through MCP, then prints the results.

## Working with files, browsers, and speech

### Web and LAN workflows

`fetch_page`, `convert_document`, `pdf_extract_text`, `extract_video`, `download_media`, and `transcribe_audio` accept local-network URLs. `synthesize_speech` can fetch its reference voice clip from a LAN URL too. The usual format and source requirements still apply, including yt-dlp support for video and media downloads. Addresses must be reachable from the toolbox containers.

The `cf_*` browser tools also open LAN addresses supplied by your agent, including private IPs, Tailscale addresses, and `.local` names. A LAN page can load local resources and navigate to other LAN pages. A page from the internet cannot make that browser reach the LAN through requests, frames, redirects, or navigation. Camoufox refuses localhost and loopback addresses. These rules are implemented in the [LAN access patch](gateway/camoufox-mcp/patch-lan-access.py).

`fetch_page` uses a separate browser. It opens the address the agent supplies, including LAN addresses, while retaining the browser's same-origin protections against a page reading another site's responses without permission. Each call gets its own browser context. See the [Crawl4AI patch](crawl4ai/patch-crawl4ai.py).

Try these with your own reachable addresses:

> Open my homelab dashboard with the browser and summarize the service status shown there.

> Extract the text from the PDF at this NAS URL, then summarize it.

> Transcribe the recording at this LAN URL, then turn the summary into spoken audio.

### Files, sessions, and models

- **File inputs:** document, PDF, and transcription tools accept URLs or paths inside the gateway container. A path on your laptop is not automatically available inside Docker.
- **Documents:** conversion reads existing text. It does not OCR scanned pages or images. Use `pdf_extract_text` for PDFs and `convert_document` for the other document formats.
- **Browser sessions:** the Camoufox browser handles one page at a time. Close multi-step sessions when finished so other calls can use it.
- **Video:** try `extract_video` for captions before transcribing. Captions and downloads depend on what the source site makes available.
- **Transcription:** `base.en` is the default. For another language, select a model without `.en` and pass a language code, or `language="auto"` for detection.
- **Speech:** synthesis runs on CPU and is slower than real time. Split long scripts into paragraphs. For voice cloning, provide the reference clip's transcript as `reference_text` to avoid an extra transcription step.

### Transcription on an AMD GPU

On a Linux host with an AMD GPU available at `/dev/dri`, enable the included Vulkan configuration:

```bash
docker compose -f docker-compose.yml -f docker-compose.amd-gpu.yml up -d
```

Check the device group IDs with `stat -c '%g %n' /dev/dri/*` and set `VIDEO_GID` and `RENDER_GID` in `.env` if needed. [`.env.example`](.env.example) also shows how to make the override persistent. This accelerates transcription; speech synthesis stays on CPU.

## Security and privacy

**Privacy-first: no retained record of agent activity, and protections against unauthorized disclosure.** The default stack disables service container logs, Crawl4AI's page cache and log files, and Stirling-PDF's audit trail and analytics. Requests, browsing history, URLs, payloads, and responses are not saved as a persistent activity record. Temporary files and browser working data use RAM-backed storage; residual data there disappears on container restart. The watcher logs only its own fixed test jobs. Model caches, Stirling settings, and health results persist in Docker volumes.

The browser protections above preserve agent-directed LAN access while limiting what pages can read or reach. Transcription and speech generation run locally. Searches and URL fetches contact the requested services, which can keep their own records. Model downloads, browser startup traffic, and scheduled checks also use the network. Host swap can write RAM contents to disk, and enabling debug logs can retain request content. See [privacy details](docs/operations.md#privacy-details) for storage behavior and outbound traffic.

The MCP endpoint is unauthenticated. Only give trusted clients network access to it. The [operations guide](docs/operations.md#how-it-fits-together) explains the published ports and how to close the optional PDF web UI.

## Keep it running, make it yours

Use `toolbox_status` from your agent or `docker logs tb-watch` from your shell to inspect health. The services use `restart: unless-stopped`, so they return when Docker starts unless you stopped them.

- [Operations guide](docs/operations.md): maintenance, pinned updates, model volumes, and debugging.
- [Agent guide](AGENT_HANDOFF.md): tool selection and complete workflow recipes.
- [Adding a tool](docs/operations.md#adding-a-tool): extend the gateway and add a real-job check.

Found a bug or have a useful tool to add? [Open an issue](https://github.com/Ruashots/agentic-toolbox-mcp/issues) or contribute a pull request. If this belongs in your agent setup, **star the repo** so you can find it again.

[MIT licensed](LICENSE). Bundled third-party software and models retain their own licenses.

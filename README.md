# toolbox

A single MCP gateway endpoint that aggregates web/doc/media/browser-automation tools for any agent on your LAN. Built so adding a capability once makes it available to every connected agent — no per-agent installs, no skill drift, no version churn across agents.

Lives on a Proxmox LXC. Exposes one URL. Any MCP-capable agent connects and gets the full toolset.

## What's inside

| Tool | Backed by | What it does |
|---|---|---|
| `search_web` | SearXNG | Federated meta-search, 30+ engines, no rate limits |
| `fetch_page` | Crawl4AI (Playwright) | JS-rendered page → clean markdown |
| `convert_document` | Markitdown | PDF/DOCX/PPTX/XLSX/HTML/image-OCR → markdown |
| `extract_video` | yt-dlp | Metadata + auto-transcript from YouTube/podcasts/1000+ sites |
| `download_media` | yt-dlp + Node | Download the real audio/video **file** (base64). Genuine best stream — no low-quality `format 18` fallback |
| `pdf_extract_text` | Stirling-PDF | Plain text from PDF |
| `transcribe_audio` | whisper.cpp + Vulkan | GPU STT (AMD 5700 XT). All standard models tiny→large-v3-turbo |
| `synthesize_speech` | OmniVoice (CPU) | TTS in 600+ languages, optional voice cloning from a 3-10 s sample |
| `cf_*` (17 tools) | whit3rabbit/camoufox-mcp | Stealth Firefox automation. Use when normal browsers get bot-blocked |
| `ping` | — | Health check / hot-reload smoke test |

The three media tools split by what they hand back: `extract_video` → metadata/transcript,
`transcribe_audio` → text, `download_media` → the actual file (base64). `download_media` uses the
container's Node JS runtime so yt-dlp sees full formats (avoids YouTube's degraded `format 18`).

Speech goes both directions: `transcribe_audio` for speech→text, `synthesize_speech` for text→speech.
The asymmetry is deliberate and worth knowing before you plan around it — STT is GPU-accelerated and
faster than real time, TTS runs on CPU and is slower than real time. See the TTS section below.

Browser tools split by job:
- `fetch_page` → fast Crawl4AI fetch with JS rendering, the 90% case
- `cf_browse` → Camoufox-backed when Crawl4AI gets caught (Cloudflare Turnstile, DataDome, fingerprint walls)
- `cf_browse_session_*` → multi-step flows (click load-more, fill forms, navigate auth)

## Architecture

One FastMCP gateway, multiple backing services on the same Docker network:

```
agents ── HTTP/MCP ──▶ tb-gateway ──┬──▶ tb-searxng
                                    ├──▶ tb-crawl4ai
                                    ├──▶ tb-stirling
                                    ├──▶ tb-whisper (AMD GPU passthrough)
                                    ├──▶ tb-tts (CPU)
                                    └──▶ camoufox-mcp (subprocess, mounted via FastMCP)
```

Three patterns that earn their keep:

**FastMCP `mount(create_proxy(...))`** — third-party MCP servers (Camoufox-mcp) are spawned as STDIO subprocesses inside the gateway. Their tools surface through our single endpoint with a namespace prefix. We don't reimplement them; we expose them.

**Eager init at startup** — the gateway calls `list_tools()` on itself in-process before binding the HTTP server. Forces the proxy subprocess to spawn and register its tools so the first external client connection already sees the full catalog. Without this, the proxy populates lazily and clients miss tools on their initial enumeration.

**Health watcher** — background task polls a proxy tool every 30s. On two consecutive failures (camoufox subprocess crashed from an unhandledRejection), `os._exit(1)` triggers Docker's `restart: unless-stopped` to respawn the gateway. ~10s self-heal per crash.

## Reproducing

Prereqs:
- A Proxmox host (or any Docker host)
- An LXC / VM with at least 4 GB RAM, 8 GB+ if running Whisper
- Docker + docker compose v2
- For Whisper GPU: an AMD GPU passed through with `/dev/dri/card*` + `/dev/dri/renderD128` (or skip Whisper)

Steps:

```bash
# Clone
git clone <this-repo> toolbox
cd toolbox

# Start the stack (first build ~10-15 min — Whisper builds whisper.cpp with Vulkan,
# Camoufox downloads a 300 MB patched Firefox on first cf_browse call)
docker compose up -d --build

# Wait for gateway to log "Uvicorn running" — eager init takes ~10s
docker compose logs -f gateway
```

Wire it into your agent:

```bash
# Claude Code (user scope, available across all projects)
claude mcp add --transport http --scope user toolbox http://<host-ip>:8000/mcp

# Other MCP clients: configure with HTTP transport pointing at http://<host-ip>:8000/mcp
```

## Hard-won gotchas (read these or rediscover them yourself)

These cost us a lot of rebuilds. Documented so you don't repeat them.

1. **pnpm 10 silently breaks native modules.** `better-sqlite3` (camoufox-mcp transitive dep) needs `node-gyp` to build its `.node` binding. pnpm 10 blocks lifecycle scripts by default → no postinstall → no compiled binding → cf_browse fails at runtime with "Could not locate the bindings file." **Pin pnpm 9.** Already done in the Dockerfile; don't bump it without retesting.

2. **Pin `camoufox-mcp-server` to a known version.** `@latest` causes transitive-dep drift between rebuilds. We pin `2.0.7`.

3. **Don't use `npx` to spawn camoufox-mcp from the FastMCP proxy.** npx's package cache fragments across container restarts and you'll hit ERR_MODULE_NOT_FOUND. Use the binary that pnpm puts in `/usr/local/bin/camoufox-mcp-server`.

4. **No volume mount on `/root/.cache/camoufox`.** Camoufox-mcp tries to `rmdir` its cache subdir at startup as a cleanup step. Docker volume mount points can't be rmdir'd → EBUSY → unhandledRejection → subprocess death. Let the Firefox binary live in the container writable layer instead; container restart + LXC reboot preserve it. Only `docker compose up --build` wipes it (and triggers a one-time 300 MB re-fetch).

5. **Camoufox needs Xvfb.** "Virtual headless" mode uses a fake X display for fingerprint-resistance tricks. `apt install xvfb` is required even though no display is connected.

6. **whisper.cpp + Vulkan needs the full SPIR-V chain.** `glslc` (only in Ubuntu 24.04+ main), `spirv-headers`, `glslang-dev`, `libshaderc-dev`. Use `ubuntu:24.04` as the builder base. Match runtime base for glibc (whisper-cli built on 24.04 needs `GLIBC_2.38` / `GLIBCXX_3.4.32`).

7. **Constrain `cmake --build -j` parallelism.** Whisper's Vulkan headers are template-heavy — each `cc1plus` peaks at ~3 GB. All-cores parallel will OOM in an 8 GB LXC. `-j4` keeps it under the cap. Even at `-j4` this compile can OOM if the other containers are running and eating RAM — so don't trigger a whisper rebuild unless you mean to. The sneaky way to trigger it accidentally is `docker compose up --build gateway`: because `gateway depends_on whisper`, that rebuilds whisper too. To redeploy only a gateway code change, use `docker compose build gateway && docker compose up -d --no-deps gateway` (see Operations).

8. **TTS is CPU-only because the GPU is AMD.** OmniVoice is a PyTorch model with CUDA / Apple MPS / Intel XPU backends and no ROCm path. Don't try to point it at the AMD card — the reason whisper works on that GPU is that whisper.cpp ships hand-written Vulkan compute shaders, which is a completely different approach. The upside is that OmniVoice's dependency list has no `flash-attn` and no `vllm`, which is what makes a plain CPU build possible at all; if a future TTS model pins either, it won't run here.

9. **First call to `cf_browse` after a `docker compose up --build` may fail** with "Version information not found at .../version.json" — Firefox download race. Retry once; subsequent calls work.

## Text-to-speech

`synthesize_speech` wraps [OmniVoice](https://github.com/k2-fsa/OmniVoice) — Apache-2.0, 600+
languages, zero-shot voice cloning from a short reference clip.

**It runs on CPU, and that is a deliberate trade.** OmniVoice ships CUDA, Apple MPS and Intel XPU
backends; there is no ROCm path, and this stack's GPU is AMD. The options were "no TTS in the
toolbox" or "TTS that is slower than real time." The second is more useful, because what agents
actually ask for is narration and voiceover — batch jobs where a few minutes of wall clock is
irrelevant. What it is *not* suitable for is anything interactive or streaming.

Measured on an 8-core CPU allocation, at the default 32 diffusion steps:

| Mode | Real-time factor | 60 s of speech takes |
|---|---|---|
| Default voice, `steps=16` | ~2.2x | ~2 min |
| Default voice, `steps=32` | ~4.5x | ~4.5 min |
| Cloned voice, `steps=32` | ~7x | ~7 min |

Two things that matter more than the raw numbers:

**Batch the whole script in one call.** Per-call overhead dominates short requests — a 3.5 s line
measured 13x real time while a 40 s passage measured 6.9x. Same model, same settings. Synthesising
line by line wastes most of the compute.

**Pass `reference_text` when cloning.** Without it the model transcribes the reference clip with
Whisper first, which on CPU costs more than the synthesis. With it, cloning is a single pass.

The model is ~3 GB and downloads on first use into the `tts-models` volume. A cold start (download
included) is ~35 s; afterwards the model stays resident and warm loads are ~1.5 s. Requests are
serialised — the model is not re-entrant, and parallel requests on a CPU box would only contend for
the same cores.

For voice cloning, use a 3-10 second reference in the *same language* as the text you want spoken.
A cross-language reference works, but the output carries the reference language's accent.

## Operations

```bash
# Tail any service
docker compose logs -f gateway      # toolbox gateway logs
docker compose logs -f whisper      # Whisper inference logs
docker compose logs -f crawl4ai     # browser pool

# Restart one service (other tools stay up)
docker compose restart gateway

# Pull latest base images (Stirling/SearXNG/Crawl4AI)
docker compose pull && docker compose up -d

# Rebuild gateway after a code/dep change.
# IMPORTANT: build then recreate with --no-deps. Do NOT use `up --build gateway`:
# gateway `depends_on: whisper`, so `up --build` ALSO rebuilds the whisper image,
# whose whisper.cpp+Vulkan compile OOM-kills cc1plus in an 8 GB LXC (see gotcha #7).
# The gateway image only needs the new server.py copied — a ~3 s rebuild.
docker compose build gateway          # builds gateway only, never touches deps
docker compose up -d --no-deps gateway  # recreates only the gateway container

# Verify all tools are live
docker exec tb-gateway python -c "
import asyncio
from fastmcp import Client
async def main():
    async with Client('http://localhost:8000/mcp') as c:
        print(len(await c.list_tools()), 'tools')
asyncio.run(main())
"
```

## Adding a new tool

The scalable pattern. When a capability gap repeats across agents:

1. If it needs a new container: add to `docker-compose.yml` on the `toolbox` network.
2. If it's a Python dep: add to `gateway/requirements.txt`.
3. Add a `@mcp.tool` function to `gateway/server.py`. Write the docstring for a small model — first line is the summary an agent reads to decide whether to call it.
4. `docker compose build gateway && docker compose up -d --no-deps gateway` (~3 s rebuild + ~5-10 sec downtime, well within MCP reconnect window). **Not** `up --build gateway` — that drags in a whisper Vulkan recompile that OOMs the LXC (see Operations note + gotcha #7).
5. Connected MCP clients pick up the new tool via `tools/list_changed` notification or on next refresh. For Claude Code: `/reload-plugins`.

For wrapping an external MCP server (like we did with Camoufox), mount it through FastMCP's `create_proxy()` instead of reimplementing. See `gateway/server.py` for the pattern.

## Boot survival

Verified end-to-end:
- LXC `onboot: 1` (Proxmox), Docker enabled inside, all containers `restart: unless-stopped`
- Gateway eager-init repopulates all tools at startup
- Firefox binary persists in container writable layer
- LXC reboot → all tools live in ~10-15 sec, no manual intervention

## Agents

For wiring an agent to actually USE this toolbox effectively (tool selection rules, workflow recipes, gotchas to tell the agent about), see [AGENT_HANDOFF.md](./AGENT_HANDOFF.md). It's written so any MCP-capable agent — Claude Code, Hermes-style harnesses, custom local-model agents — can be onboarded with one document.

## License

MIT

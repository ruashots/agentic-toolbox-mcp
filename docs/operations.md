# Running the toolbox

How the toolbox is put together, how to keep it healthy, how to change it, and
what went wrong before so you do not repeat it. For installing it, see the
[README](../README.md).

- [How it fits together](#how-it-fits-together)
- [Health checks](#health-checks)
- [Everyday commands](#everyday-commands)
- [Pinned versions](#pinned-versions)
- [Updating a pinned part](#updating-a-pinned-part)
- [Debugging without logs](#debugging-without-logs)
- [Adding a tool](#adding-a-tool)
- [Starting on boot](#starting-on-boot)
- [Lessons learned the hard way](#lessons-learned-the-hard-way)
- [Making the README images](#making-the-readme-images)

## How it fits together

One gateway container speaks MCP to your agents. Each tool is either code in the
gateway (`gateway/server.py`) or a call to one of the services next to it on the
same Docker network:

```
agents ── MCP over HTTP ──▶ tb-gateway :8000 ──┬──▶ tb-searxng    search_web
                                               ├──▶ tb-crawl4ai   fetch_page
                                               ├──▶ tb-stirling   pdf_extract_text
                                               ├──▶ tb-whisper    transcribe_audio
                                               ├──▶ tb-tts        synthesize_speech
                                               └──▶ camoufox-mcp  the 17 cf_* tools (a process inside the gateway)

tb-watch ── MCP over HTTP ──▶ tb-gateway   one real job per tool, on a schedule
    └── writes status.json, which the gateway reads to explain failures to agents
```

`convert_document`, `extract_video` and `download_media` run inside the gateway
itself (Markitdown and yt-dlp).

Three patterns hold it together:

- **Other MCP servers are mounted, not rewritten.** The browser tools come from
  [camoufox-mcp](https://github.com/whit3rabbit/camoufox-mcp), started as a
  subprocess and mounted with FastMCP's `create_proxy()`. Its tools appear on the
  same endpoint with a `cf_` prefix.
- **Tools are listed before the first client connects.** At startup the gateway
  lists its own tools once, which starts the browser subprocess. Without this, an
  agent that connects first sees only the gateway's own tools and none of the
  `cf_*` ones.
- **A dead browser server comes back by itself.** If the browser server process
  dies, the browser call that was running fails with "Connection closed", and the
  next browser call starts a new process (FastMCP does this); the gateway keeps
  running. Whether pages actually load is what the watcher checks.

## Health checks

`tb-watch` (code in `gateway/watch.py`) calls the tools through the public MCP
endpoint, the same way an agent does, with fixed test inputs, and checks the answer.
There is one check for each tool except `toolbox_status` (it reports the checks),
and one for all the `cf_*` tools, which share a browser:

| Check | Test job | Every |
|---|---|---|
| ping | `ping` returns pong | 1 min |
| search_web | a search for "wikipedia" returns results | 10 min |
| fetch_page | wikipedia.org contains "Wikipedia" | 10 min |
| cf_browse | the stealth browser loads python.org and the answer has its page title (one check covers all 17 `cf_*` tools: they share one browser) | 10 min |
| convert_document | `gateway/fixtures/check.html` converts and its marker is found | 30 min |
| pdf_extract_text | `gateway/fixtures/check.pdf` gives its marker back | 30 min |
| transcribe_audio | `gateway/fixtures/check.wav` transcribes to "...health check..." | 1 h |
| synthesize_speech | "Health check." comes back as real audio | 1 h |
| extract_video | metadata of the first video on YouTube ("Me at the zoo", 19 s) | 6 h |
| download_media | the audio of that same video | 6 h |

- A tool is marked **down** after 2 failed checks in a row (a first failure is
  checked again after 60 seconds) and **up** after one good check. While a tool is
  down it is checked again at least every 5 minutes.
- A browser check that fails only because it waited for the browser ("Timed out
  waiting for a browse slot" and the like: another call or an open multi-step
  session had it) counts as neither a pass nor a failure, and is tried again a
  minute later. An agent whose browser call fails that way is told the browser is
  busy, not that it is down.
- Agents see the result in the failing call itself. Every tool error ends with a
  line saying whether the tool is a known outage (since when; retrying will not
  help), failed its latest check, or passed it (one retry is reasonable), and which
  tool to use instead. The `toolbox_status` tool shows every tool at once.
- The status lives in the `tb-health` volume, so a watcher restart does not forget
  an outage.
- `docker logs tb-watch` shows each check's result. That log only ever contains the
  watcher's own fixed test jobs.

Until the speech model has finished downloading (about 3 GB, on the first speech
check), `synthesize_speech` can show as down. It comes back up on its own.

## Everyday commands

Run these from the repository folder.

```bash
# What works right now (the watcher's latest results)
docker logs --tail 30 tb-watch

# Restart one service. Restarting tts or whisper only affects its own tool;
# restarting the gateway takes every tool offline for a few seconds.
docker compose restart tts

# Rebuild and restart only the gateway after changing gateway/server.py.
# Not `docker compose up --build gateway`: through depends_on that also rebuilds
# whisper, whose compile is slow and memory hungry.
docker compose build gateway && docker compose up -d --no-deps gateway

# The watcher has its own image. After changing gateway/watch.py or the fixtures:
docker compose build gateway watch && docker compose up -d --no-deps gateway watch

# List the tools the gateway serves
docker exec tb-gateway python -c "
import asyncio
from fastmcp import Client
async def main():
    async with Client('http://localhost:8000/mcp') as c:
        print(len(await c.list_tools()), 'tools')
asyncio.run(main())
"

# Free the disk space the build cache takes. It asks first. It removes ALL of
# Docker's build cache on this machine, other projects' included, and the next
# rebuild of any image starts from scratch. On fresh installs Docker reported 17 to
# 18 GB, and the disk got back 8 to 13 GB (the images share part of that data).
docker builder prune -a
```

`docker compose pull` changes nothing here: every image is pinned. To update one,
see [Updating a pinned part](#updating-a-pinned-part).

## Pinned versions

Before these were pinned, parts of the toolbox fetched "the newest" version
whenever an image was rebuilt or a tool was first used. One routine rebuild pulled
a newer Camoufox browser that the browser driver could not launch, and every `cf_*`
call failed for seven weeks while the old health check (is the process alive?)
stayed green. Now these are pinned and checked:

| Part | Pinned how | Where |
|---|---|---|
| Camoufox browser 156.0.1-beta.33 | official release zip, sha256 checked at build | `gateway/Dockerfile` (`CAMOUFOX_*`), `gateway/camoufox-mcp/install-browser.sh` |
| uBlock Origin 1.75.0 (loaded by the browser), with the filter lists it shipped with | addons.mozilla.org file, sha256 checked; list updates switched off through Firefox's policies | `gateway/Dockerfile` (`UBO_*`), `gateway/camoufox-mcp/install-browser.sh` |
| Browser driver: camoufox-mcp-server 2.0.7, camoufox-js 0.10.2, playwright-core 1.51.1 and everything under them | pnpm lockfile, `--frozen-lockfile` | `gateway/camoufox-mcp/` |
| camoufox-js fix for browser 156 | build-time patch; the build fails if it does not apply | `gateway/camoufox-mcp/patch-camoufox-js.sh` |
| Browser fix for DNS-blocked trackers | build-time patch; the build fails if it does not apply | `gateway/camoufox-mcp/patch-dns-sinkhole.py` |
| Gateway Python packages | full lock file | `gateway/requirements.lock` |
| Node | exact package `22.23.3-1nodesource1` | `gateway/Dockerfile` |
| Base images | `@sha256:` digests | every Dockerfile |
| SearXNG, Crawl4AI, Stirling-PDF images | `@sha256:` digests | `docker-compose.yml`, `crawl4ai/Dockerfile` |
| Crawl4AI's browser settings: no web-security bypass, no calls of its own to Google, one browser context per call | build-time patch; the build fails if it does not apply, or if Playwright's own list of switched-off features grows | `crawl4ai/patch-crawl4ai.py` |
| whisper.cpp | git commit | `whisper/Dockerfile` (`WHISPER_CPP_COMMIT`) |
| Whisper models for transcription | one commit of Hugging Face's `ggerganov/whisper.cpp`; each file's sha256 checked after download, and a file that does not match is deleted | `whisper/models.py` |
| OmniVoice | git commit | `tts/Dockerfile` (`OMNIVOICE_COMMIT`) |
| Whisper and TTS Python packages, PyTorch included | full lock files | `whisper/requirements.lock`, `tts/requirements.lock` |
| OmniVoice voice model, and the Whisper model it uses to transcribe cloning clips | Hugging Face revision; downloaded once, then loaded from the `tts-models` volume without contacting Hugging Face | `tts/server.py` (`MODEL_REVISION`, `ASR_REVISION`) |

The gateway build also launches the browser and renders a page
(`gateway/camoufox-mcp/selftest.mjs`). If the driver and the browser do not work
together, the build fails and the running gateway is never replaced.

Not pinned, on purpose or by nature:

- **System packages** inside each image come from its Debian or Ubuntu release at
  build time, so a rebuild gets that release's security updates.
- **The GeoIP database** the browser uses to match its time zone and language to
  your public IP is downloaded at build time. Its only source keeps just its last
  two releases, so a checksum would break within days.
- **The NodeSource setup script**, which adds Node's package source, is fetched at
  build time. The Node package it installs is pinned.

## Updating a pinned part

Change one part at a time:

1. Keep the current image for rollback, for example
   `docker tag toolbox-gateway:latest toolbox-gateway:before-2026-12-01`.
2. Make the change, then `docker compose build <service>` and
   `docker compose up -d --no-deps <service>`.
3. Check `toolbox_status`, or wait for the watcher to run its checks.
4. Commit the change.

Per part:

- **yt-dlp** is the one most likely to need it, because YouTube changes break old
  versions (the watcher reports it). Set `yt-dlp==<version>` in
  `gateway/requirements.lock` and rebuild the gateway.
- **A Python package:** edit its line in the right `requirements.lock`. For a bigger
  change, install in a scratch container, test, then `pip freeze > requirements.lock`.
  For the gateway, delete the `SpeechRecognition` line afterwards: with it present,
  `convert_document` sends audio files to Google's speech service.
- **A base image:** pull the new tag, read its digest with
  `docker inspect --format '{{index .RepoDigests 0}}' <image>:<tag>`, and replace the
  digest in the Dockerfile.
- **SearXNG, Crawl4AI, Stirling-PDF:** the same, in `docker-compose.yml`
  (Crawl4AI: in `crawl4ai/Dockerfile`; its privacy edits and `patch-crawl4ai.py`
  must still apply or the build fails).
- **whisper.cpp, OmniVoice:** change the commit in the Dockerfile `ARG`.
- **Whisper models:** take the new commit and each file's sha256 from the
  `ggerganov/whisper.cpp` page on Hugging Face (Files and versions), and set
  `REVISION` and the checksums in `whisper/models.py`. Models already in the
  `whisper-models` volume are kept; remove one to have it downloaded again.
- **The voice model** (or the model that transcribes cloning clips): take the new
  commit from the model's Hugging Face page (Files and versions, then History), set
  `MODEL_REVISION` (or `ASR_REVISION`) in `tts/server.py`, and rebuild `tts`. The next
  speech request downloads that revision; the old one stays in the `tts-models`
  volume until you remove it.
- **The browser driver:** in a scratch folder, run
  `pnpm add camoufox-mcp-server@X playwright-core@Y` with `auto-install-peers=false`,
  then copy `package.json` and `pnpm-lock.yaml` into `gateway/camoufox-mcp/`. Check
  whether the camoufox-js patch still applies, and whether it is still needed.
- **The Camoufox browser:** a browser build must match the driver and its
  playwright-core version (they speak one protocol). Set `CAMOUFOX_VERSION`,
  `CAMOUFOX_RELEASE` and `CAMOUFOX_SHA256` in `gateway/Dockerfile` to the new release
  (GitHub shows each release file's sha256). The build's selftest tells you at once
  whether it works.

### Building without downloading the browser

The browser zip is about 1.3 GB. If its release ever disappears, or to avoid
downloading it again, put the same file in `gateway/vendor/` first. The build uses
it and still checks its sha256:

```bash
sha256sum gateway/vendor/camoufox-156.0.1-beta.33-lin.x86_64.zip
# 730d731153b2a16cac238a0b4a7f849c04d1fd6181c09ec6af29695af0407af8
```

The rest of the build still downloads its packages, uBlock Origin and the GeoIP
database.

## Debugging without logs

Container logs are off, and that is deliberate (see the README's privacy section).
The first step rarely needs them: `toolbox_status` and `docker logs tb-watch` say
which tool fails and the error it gave on a fixed test input.

When you do need a service's own log, turn it on for that one service, reproduce
the problem, read, and turn it off again. These logs can contain what agents asked
for, so keep them on as briefly as you can:

```bash
docker compose -f docker-compose.yml -f docker-compose.debug-logs.yml up -d --no-deps gateway
docker logs tb-gateway
docker compose up -d --no-deps --force-recreate gateway   # logs off again; the log file is deleted
```

## Adding a tool

When the same missing capability keeps coming up across agents:

1. If it needs a new container, add it to `docker-compose.yml` on the `toolbox`
   network, with `<<: *private` so it keeps no logs.
2. If it needs a Python package, add it at an exact version to
   `gateway/requirements.lock`, with its own dependencies.
3. Add an `@mcp.tool` function to `gateway/server.py`. Write the docstring for a
   small model: its first line is what an agent reads to decide whether to call it.
4. Add a real-job check to `CHECKS` and a short description to `DESCRIPTIONS` in
   `gateway/watch.py`, and what to use instead while it is down to `_ALTERNATIVES`
   in `server.py`.
5. `docker compose build gateway watch && docker compose up -d --no-deps gateway watch`
   (the watcher has its own image, so it needs the rebuild too).
6. Agents see the new tool when they next connect. In Claude Code, start a new session.

To wrap an existing MCP server, mount it with FastMCP's `create_proxy()` as the
browser server is, instead of rewriting it.

## Starting on boot

Every container has `restart: unless-stopped`, so the toolbox comes back whenever
Docker starts. Make sure Docker itself starts on boot (on most Linux systems:
`sudo systemctl enable docker`). The gateway lists all its tools again at startup,
and the browser is part of the image, so nothing needs to be done by hand after a
reboot.

## Lessons learned the hard way

1. **pnpm 10 silently breaks native modules.** `better-sqlite3` (a camoufox-mcp
   dependency) needs its install script to build. pnpm 10 skips install scripts by
   default, so the browser tools fail at run time with "Could not locate the bindings
   file". The Dockerfile pins pnpm 9.
2. **Never install camoufox-mcp-server at `@latest`.** Its dependencies drift between
   rebuilds. It is pinned and installed from a lockfile.
3. **Do not start camoufox-mcp with `npx`.** npx's package cache does not survive
   container restarts intact, and you get `ERR_MODULE_NOT_FOUND`. The gateway starts
   the installed binary directly.
4. **No volume on `/root/.cache/camoufox`.** camoufox-mcp removes a folder there at
   startup, which fails on a volume mount point (EBUSY) and kills the subprocess. The
   browser is baked into the image instead.
5. **Camoufox needs Xvfb.** Its "virtual headless" mode draws to a fake display for
   its fingerprint protection, so `xvfb` must be installed even with no screen.
6. **whisper.cpp with Vulkan needs the full SPIR-V toolchain.** `glslc` (in Ubuntu
   24.04's main archive), `spirv-headers`, `glslang-dev` and `libshaderc-dev`. Build
   and run on the same Ubuntu release, or the binary misses glibc symbols.
7. **Keep the whisper.cpp compile at `-j4`.** Each compiler process can reach about
   3 GB; using every core runs an 8 GB machine out of memory.
8. **The browser driver picks "the newest" browser on its own.** camoufox-js downloads
   the newest release above a minimum version, with no upper limit. That is how a
   rebuild installed a browser the driver could not launch. The browser is now
   installed at build time from one pinned release, so the driver never downloads.
9. **"Browser available" means nothing.** `cf_camoufox_status` reported the browser
   as available for seven weeks while every page load failed. Only a real page load
   proves browsing works, so the watcher loads python.org and looks for its page
   title (not just the word "python", which every answer contains in its URL).
10. **Ad-blocking DNS used to fail most pages.** DNS filters such as AdGuard Home and
    Pi-hole answer 0.0.0.0 or 127.0.0.1 for tracker hosts. The browser's network
    guard blocked those requests, which is right, and then failed the whole page,
    which is not: 7 of 10 common sites failed this way. `patch-dns-sinkhole.py`
    keeps blocking them and lets the page load. LAN and localhost addresses are
    still refused.
11. **Crawl4AI's browser shipped with its protections off.** Its default config
    starts Chromium with `--disable-web-security`, which lets a script on any page
    read any other site's answers, including services on your network.
    `patch-crawl4ai.py` removes it and `--allow-insecure-localhost`, and keeps
    `--ignore-certificate-errors` for self-signed certificates on home networks.
12. **Chromium keeps only the last `--disable-features`.** Playwright switches off a
    list of features, including some that call Google, and Crawl4AI then passes a
    shorter list of its own, which replaced Playwright's. Even with both, the
    browser asked accounts.google.com, www.google.com and clients2.google.com at
    start, registered for push messages at android.clients.google.com, and sent
    the form fingerprints of pages it read to content-autofill.googleapis.com.
    `patch-crawl4ai.py` passes one list that holds everything, points Chrome's own
    Google services at an address where nothing answers, and fails the build if a
    Playwright update adds a feature the list lacks.

## Making the README images

`docs/media/demo.svg` is the real output of `gateway/demo.py` against a fresh
install:

```bash
docker exec -e DEMO_SVG=/app/demo.svg tb-gateway python demo.py
docker cp tb-gateway:/app/demo.svg docs/media/demo.svg
```

`docs/media/social-card.png` (1280 x 640, for GitHub's social preview) is rendered
from `docs/media/social-card.html` by the toolbox's own browser:

```bash
docker cp docs/media/social-card.html tb-gateway:/app/social-card.html
docker cp docs/media/render-card.mjs tb-gateway:/app/render-card.mjs
docker exec tb-gateway node /app/render-card.mjs
docker cp tb-gateway:/app/social-card.png docs/media/social-card.png
```

Both write to `/app` in the container, not `/tmp`: `/tmp` lives in RAM there, and
`docker cp` cannot reach it.

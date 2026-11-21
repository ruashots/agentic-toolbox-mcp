# Running the toolbox

How the toolbox is put together, how to keep it healthy, how to change it, and
what went wrong before so you do not repeat it. For installing it, see the
[README](../README.md).

- [How it fits together](#how-it-fits-together)
- [Health checks](#health-checks)
- [Everyday commands](#everyday-commands)
- [Pinned versions](#pinned-versions)
- [Updating a pinned part](#updating-a-pinned-part)
- [Privacy details](#privacy-details)
- [Models and backups](#models-and-backups)
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

Two ports are published on the host:

- **8000**, the MCP endpoint, open to your network (agents on other machines connect
  here). There is no login; see the README's security and privacy section.
- **8085**, Stirling-PDF's own web page, with its login off, also open to your
  network. The gateway reaches Stirling over the internal Docker network, so the
  toolbox works the same with this port closed: delete the two `ports` lines under
  `stirling-pdf` in `docker-compose.yml` to close it.

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
and one for all the `cf_*` tools, which share a browser. Two more checks read a
page on your own network with the same page-reading tools:

| Check | Test job | Every |
|---|---|---|
| ping | `ping` returns pong | 1 min |
| search_web | searches "docker compose healthcheck", asks for 5 results, and needs at least 3 with at least two of those words in their title, snippet or URL (case ignored) | 10 min |
| fetch_page | wikipedia.org contains "Wikipedia" | 10 min |
| cf_browse | the stealth browser loads python.org and the answer has its page title (one check covers all 17 `cf_*` tools: they share one browser) | 10 min |
| convert_document | `gateway/fixtures/check.html` converts and its marker is found | 30 min |
| pdf_extract_text | `gateway/fixtures/check.pdf` gives its marker back | 30 min |
| transcribe_audio | `gateway/fixtures/check.wav` transcribes to "...health check..." | 1 h |
| synthesize_speech | "Health check." comes back as real audio | 1 h |
| extract_video | metadata of the first video on YouTube ("Me at the zoo", 19 s) | 6 h |
| download_media | the audio of that same video | 6 h |
| fetch_page_lan | `fetch_page` reads the network page and the answer contains the expected text | 10 min |
| cf_browse_lan | `cf_browse` reads the same network page and the answer contains the expected text | 10 min |

The network checks default to `http://stirling-pdf:8080/`, the toolbox's own
Stirling-PDF page, and look for "No files yet" (case ignored). This checks a private
address and a page that draws itself with JavaScript. To check another page, set
`LAN_CHECK_URL` and `LAN_CHECK_TEXT` in `.env`; choose text the page always shows.
Apply the settings with `docker compose up -d --no-deps --force-recreate watch`.
These are separate checks in `toolbox_status`, not extra tools. In `status.json`,
`covers` maps tools to their main checks and `lan_checks` maps `fetch_page` and
`cf_browse` to their network checks.

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
  tool to use instead. For a failed `fetch_page` or `cf_*` call, if its network check is down,
  the note gives the failure start time and warns against retrying a network address.
  Browser-busy errors keep their separate note. The `toolbox_status` tool shows
  every check at once.
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
| Browser access to your network: addresses an agent gives it, and LAN pages reaching the LAN; never internet pages reaching the LAN | build-time patch, then `lan-selftest.mjs` runs the real guard against 14 made-up requests; the build fails if any answer is wrong | `gateway/camoufox-mcp/patch-lan-access.py`, `gateway/camoufox-mcp/lan-selftest.mjs` |
| Gateway Python packages | full lock file, each package with the sha256 of its files; the build refuses any other file | `gateway/requirements.lock` |
| Node 22.23.3 and pnpm 9.15.9 | the nodejs.org and npm registry files, sha256 and sha512 checked at build | `gateway/Dockerfile` (`NODE_*`, `PNPM_SHA512`) |
| Base images | `@sha256:` digests | every Dockerfile |
| SearXNG 2026.11.21, Crawl4AI, Stirling-PDF images | `@sha256:` digests | `docker-compose.yml`, `crawl4ai/Dockerfile` |
| Crawl4AI's browser settings: no web-security bypass, no calls of its own to Google, one browser context per call | build-time patch; the build fails if it does not apply, or if Playwright's own list of switched-off features grows | `crawl4ai/patch-crawl4ai.py` |
| whisper.cpp | git commit | `whisper/Dockerfile` (`WHISPER_CPP_COMMIT`) |
| Whisper models for transcription | one commit of Hugging Face's `ggerganov/whisper.cpp`; each file's sha256 checked after download, and a file that does not match is deleted | `whisper/models.py` |
| OmniVoice | git commit | `tts/Dockerfile` (`OMNIVOICE_COMMIT`) |
| Whisper and TTS Python packages, PyTorch included | full lock files with sha256, as above; PyTorch comes only from the PyTorch CPU index and everything else only from PyPI | `whisper/requirements.lock`, `tts/requirements.lock`, `tts/requirements-torch.lock` |
| OmniVoice voice model, and the Whisper model it uses to transcribe cloning clips | Hugging Face revision; downloaded once, then loaded from the `tts-models` volume without contacting Hugging Face | `tts/server.py` (`MODEL_REVISION`, `ASR_REVISION`) |

SearXNG has Bing off and Brave, Yandex and Yahoo switched on from their defaults
(off). The comment
above `engines` in `searxng/settings.yml` records eight searches measured on
2026-11-21: on-topic results were Google CSE 79/80, Brave 78/80, Yandex 78/80,
DuckDuckGo 70/70, Yahoo 55/56 and Bing 3/80. Bing answered for just one word of the
search. Startpage, Qwant, Mojeek, Presearch, AOL and Yep refused or failed from the
same network; the defaults among them stay on. SearXNG pauses engines that keep
failing and tries them again later, so a paused engine costs nothing and comes
back on its own if the block lifts.

To replace SearXNG's placeholder `secret_key` without editing a tracked file, set
`SEARXNG_SECRET` in `.env` to a random string (for example, the output of
`openssl rand -hex 32`), then run
`docker compose up -d --no-deps --force-recreate searxng`. This setting is optional.

The gateway build also launches the browser and renders a page
(`gateway/camoufox-mcp/selftest.mjs`). If the driver and the browser do not work
together, the build fails and the running gateway is never replaced.

Not pinned, on purpose or by nature:

- **System packages** inside each image come from its Debian or Ubuntu release at
  build time, so a rebuild gets that release's security updates.
- **The GeoIP database** the browser uses to match its time zone and language to
  your public IP is downloaded at build time from a third party's mirror of MaxMind's
  GeoLite2 (the one camoufox-js itself uses), and only checked to contain MaxMind's
  marker. That mirror keeps just its last two releases, so a checksum would break
  within days. It is data, not code, read by camoufox-js to pick a time zone and
  language.

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
  `gateway/requirements.lock`, run `python3 scripts/hash_lock.py
  gateway/requirements.lock`, and rebuild the gateway.
- **A Python package:** edit its line in the right `requirements.lock`. For a bigger
  change, install in a scratch container, test, then `pip freeze > requirements.lock`.
  For the gateway, delete the `SpeechRecognition` line afterwards: with it present,
  `convert_document` sends audio files to Google's speech service. Then write the
  hashes: `python3 scripts/hash_lock.py <the lock file>` looks up the sha256 of every
  file of each pinned version. The build refuses a lock line without hashes.
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
- **Node:** set `NODE_VERSION` and `NODE_SHA256` in `gateway/Dockerfile`, taking the
  sha256 of `node-v<version>-linux-x64.tar.gz` from
  `https://nodejs.org/dist/v<version>/SHASUMS256.txt`.
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

## Privacy details

The toolbox keeps no record of what agents ask for or get back, on disk or in logs:

- Container logs are off for every service. The watcher keeps a small log of its own
  test jobs, never of agent requests.
- Downloads, uploads, converted files, audio, and the browsers' profiles and caches
  are kept in RAM and disappear when a container restarts. No core dumps or Java heap
  dumps are written, and `fetch_page`'s browser keeps no crash reports. When the
  machine runs short of memory, Linux can move what is in RAM to its swap area on
  disk; to keep these files off disk entirely, run the toolbox on a machine without
  swap, or with encrypted swap.
- Both browsers start empty for every call. A multi-step browser session keeps its
  cookies until it is closed, or after 10 minutes without a call.
- Crawl4AI's page cache, log files and Redis snapshots, Stirling-PDF's audit trail
  and analytics, the access logs of the speech services, FastMCP's update check and
  onnxruntime's usage telemetry are all off.

What leaves the machine:

- What agents ask for. Searches go to search engines through SearXNG, and pages,
  videos and files are fetched from their sites.
- The watcher's test jobs: a search for "docker compose healthcheck" and visits to
  wikipedia.org and python.org every 10 minutes, and the details and audio of one
  19-second YouTube video every 6 hours. Both page readers also check a network page
  every 10 minutes: by default it stays inside Docker at `http://stirling-pdf:8080/`;
  setting `LAN_CHECK_URL` sends those requests to the chosen address instead.
- Model downloads from Hugging Face, each only the first time it is needed: a
  Whisper model the first time it is used, the voice model on the first speech
  request, and, for a voice clone without the clip's transcript, a Whisper model
  (about 1.6 GB) for the speech service. Every model is pinned to one version, and
  a Whisper model whose checksum does not match is deleted instead of used. Once
  downloaded, the speech service starts without contacting Hugging Face.
- When SearXNG starts, it downloads the ClearURLs list of tracking parameters
  (rules1.clearurls.xyz) and uses it to strip them from result links.
- Each time the stealth browser starts: a lookup of the public IP address
  (api.ipify.org, or a similar service if that fails), which Camoufox uses to match
  the browser's time zone and language, and Firefox's media plugins, fetched the way
  stock Firefox does (aus5.mozilla.org, OpenH264 from ciscobinary.openh264.org,
  Widevine from Google's download servers). Camoufox keeps this so that sites cannot
  tell it apart from stock Firefox.

What websites and search engines record about the requests they receive is outside
the toolbox's control.

## Models and backups

The toolbox holds no data of yours. Every Docker volume holds files it downloads or
rebuilds on its own:

| Volume | Holds | Size |
|---|---|---|
| `toolbox_tts-models` | the OmniVoice voice model | about 3 GB |
| `toolbox_whisper-models` | the Whisper models used so far | 148 MB to a few GB |
| `toolbox_stirling-data` | Stirling-PDF's settings and its user database (two built-in accounts, no record of files) | under 1 MB |
| `toolbox_tb-health` | the watcher's latest results | a few KB |

Backing up a model volume only saves the download on a new install:

```bash
# Back up
docker run --rm -v toolbox_tts-models:/v:ro -v "$PWD":/backup alpine tar czf /backup/tts-models.tgz -C /v .

# Restore, on a new install, before its first start
docker compose up --no-start --build
docker run --rm -v toolbox_tts-models:/v -v "$PWD":/backup alpine tar xzf /backup/tts-models.tgz -C /v
docker compose up -d
```

For Whisper models, use `whisper-models` in place of `tts-models`.
`docker compose down -v` removes the containers and every volume, models included.

## Debugging without logs

Container logs are off, and that is deliberate (see [Privacy details](#privacy-details)).
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
    keeps blocking them and lets the page load. Localhost is still refused.
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
13. **Crawl4AI read web apps before they had drawn anything.** A page that builds
    itself with JavaScript, such as Stirling-PDF's own web page, has almost no text
    when it first loads. Crawl4AI's anti-bot check then called it a block page
    (`Structural: script_heavy_shell`) and `fetch_page` failed. `patch-crawl4ai.py`
    reads such a page once more, waiting up to 10 seconds for it to show text.
    Waiting for the network to go quiet does not work: web apps keep loading files
    in the background.
14. **camoufox-mcp refused your whole network.** Its guard blocks every private
    address, so agents could not use the stealth browser on a router page, a NAS or
    a dashboard. `patch-lan-access.py` lets the browser open the network addresses
    an agent gives it, and lets a page on your network reach your network. Requests
    from internet pages to your network stay blocked: sub-requests, frames,
    redirects and navigations. The guard decides from the page that made the
    request, so a site cannot get in by later pointing its own name at a LAN
    address. The same patch narrows one upstream rule that refused all of
    192.0.0.0/16, which blocked real sites such as iana.org and icann.org; only the
    reserved 192.0.0.0/24 and 192.0.2.0/24 stay refused. `lan-selftest.mjs` checks
    all of this at build time.

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

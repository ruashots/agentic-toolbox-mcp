# Using the toolbox

A shared MCP gateway is (or is about to be) available that exposes web/doc/media/browser-automation tools to any agent on the same network. This document tells you how to get yourself wired into it, which tool to pick when, how to compose them into common workflows, and which gotchas to avoid.

---

## Before you start

Check your tool surface. If `mcp__toolbox__*` tools are already there, you're set — skip to **Capability map** and start working.

If they're missing, don't try to call them (they'll just return `Unknown tool`). Don't probe the network or guess at state. Ask the user one grounding question first:

> "Where do we stand on the toolbox? Three possibilities I can handle:
> 1. Nothing set up — I deploy the whole stack from scratch.
> 2. Toolbox is already running somewhere, just not wired into me — give me the host or URL and I'll wire it.
> 3. Already wired but my session can't see it — I'll walk through a refresh."

Then act based on their answer.

### (1) Nothing set up

You're going to deploy the toolbox. This is real blast radius: ~10 min build + ~5 GB image pulls + four long-running containers + optional GPU passthrough for Whisper. Confirm scope before kicking off.

Ask the user: *"Run it on this machine, or somewhere specific (LXC on Proxmox, dedicated VM, the laptop right here)?"*

Then, on the chosen host with Docker + docker-compose available:

```bash
git clone https://github.com/Ruashots/agentic-toolbox-mcp toolbox
cd toolbox
docker compose up -d --build
# Wait for "Uvicorn running on http://0.0.0.0:8000" in:
docker compose logs -f gateway
```

When the gateway prints `[startup] N tools registered` followed by `Uvicorn running`, grab the host's reachable IP (`hostname -I`, or whatever the user calls it on their LAN), then continue to step (2) to wire yourself in.

If the user's machine isn't a sensible host for a 24/7 stack (laptop, sandboxed environment, etc.), say so honestly — don't deploy on a bad target. Ask them to identify a host that should run it.

### (2) Already running, not wired into me

Ask the user: *"What's the gateway URL?"* — usual shape is `http://<host>:8000/mcp`.

Once you have it:

1. **Test reachability before wiring.** `curl <url>` should respond (it'll return MCP protocol JSON, not a friendly page — that's expected). If it fails with connection-refused or timeout, the toolbox isn't actually running where the user thinks; escalate back to scenario (1).
2. **Ask about auth.** Default toolbox v1 is LAN-trust — no bearer enforced. If the user says "no auth," wire the URL bare. If they hand you a token, add `Authorization: Bearer <token>` as a header in your MCP config. Don't assume either way; ask.
3. **Wire it into your runtime's MCP config:**
   - Claude Code (no auth): `claude mcp add --transport http --scope user toolbox <url>`
   - Claude Code (with bearer): `claude mcp add --transport http --scope user toolbox <url> --header "Authorization: Bearer <token>"`
   - Other MCP-capable agents: HTTP transport pointing at `<url>`, with the bearer header if provided — exact config path varies by runtime, ask the user if you don't know yours.
4. **Reload or restart your session** so the new tools surface. For Claude Code that's `/reload-plugins`; other runtimes vary.
5. **Verify with `ping()`** — should return `"pong"`. If it doesn't, jump to scenario (3).

### (3) Wired but I can't see it

Your session probably cached its tool list before the toolbox was reachable, or the proxy mounts populated after your initial connection.

- Claude Code: try `/reload-plugins` first. If `mcp__toolbox__*` still doesn't appear, fully restart the Claude process (a fresh session, not just a new conversation).
- Other runtimes: cycle the agent's MCP client connection — exact mechanism varies.
- After the restart, `ping()` should return `"pong"`. If it still fails, the toolbox itself isn't reachable from where you're running; escalate back to (2) and confirm the URL with the user.

Once `ping()` works, you have the full toolset. Continue.

---

## Capability map (pick by intent)

| You need to... | Use | Notes |
|---|---|---|
| Search the web for current information | `search_web` | Federated across 30+ engines, no rate limits, returns title/url/snippet |
| Read a normal web page | `fetch_page` | JS-rendered via Crawl4AI/Playwright. Returns clean markdown. Default choice for "give me the content at this URL." |
| Read a page that blocks normal browsers | `cf_browse` | Camoufox stealth Firefox. Use when `fetch_page` returns empty, a challenge page, or "checking your browser." See **Selection rules** below. |
| Drive a page through multiple steps (click, fill, navigate) | `cf_browse_session_start` → `cf_browse_session_navigate`/`action`/`snapshot` → `cf_browse_session_close` | Stateful browser session. Use for "click load-more then scrape" or auth flows. |
| Take a screenshot of a page | `cf_browse_screenshot` | Returns PNG/JPEG. Pair with your own vision capability to reason about it. |
| Extract structured fragments from a page | `cf_browse_links` / `cf_browse_forms` / `cf_browse_outline` / `cf_browse_find` | Cheaper than full content fetch when you only want one shape of data. |
| Convert a document to markdown | `convert_document` | URL or local path. Handles PDF/DOCX/PPTX/XLSX/HTML/images-with-OCR. **For PDFs use this for structured markdown.** |
| Get plain text from a PDF | `pdf_extract_text` | Cleaner word spacing than `convert_document` for column PDFs. Use this for "give me the raw readable text." |
| Get metadata + auto-transcript from a video URL | `extract_video` | yt-dlp under the hood, 1000+ sites supported. Returns title, uploader, duration, description, transcript-if-available. **Try this FIRST** before transcribing — many videos have free auto-captions. |
| Transcribe audio/video when there's no auto-transcript | `transcribe_audio` | Local Whisper on GPU. See **Model selection** below — bigger isn't always better. |
| Sanity-check the toolbox is reachable | `ping` | Returns `"pong"`. |

---

## Selection rules

### `fetch_page` vs `cf_browse`

Try `fetch_page` first — it's faster, lighter, and handles ~90% of pages including most JS-heavy SPAs.

Fall back to `cf_browse` when you see any of:
- Response is suspiciously short (< 200 chars) or just a skeleton
- Title or content contains "Just a moment", "Checking your browser", "Please verify", "Access denied", "Bot detected"
- Site is known to use Cloudflare Turnstile, DataDome, PerimeterX, or Akamai Bot Manager
- Crawl4AI returns an error mentioning challenge or 403

When you call `cf_browse`, set `humanize: true` for hostile targets — it enables realistic cursor movement and human-like timing that defeats most behavioral fingerprinting.

### `cf_browse` vs `cf_browse_session_*`

Single page read → `cf_browse` (browser launched and closed per call).

Multi-step interaction → start a session, do N actions, close it. Session keeps cookies, auth state, and viewport between calls.

### `convert_document` vs `pdf_extract_text`

Both can read a PDF. Different tradeoffs:
- `convert_document` (Markitdown) — preserves structure (tables become markdown tables, headings preserved). **Quirk:** column-heavy PDFs sometimes lose spaces between words (e.g. "AttentionIsAllYouNeed").
- `pdf_extract_text` (Stirling-PDF) — clean word spacing, no structure preserved. Plain text only.

Pick by what you need: structure → `convert_document`. Readable text → `pdf_extract_text`.

### `transcribe_audio` model selection

Models are downloaded lazily on first use (cached after). Pick based on content shape, not "always biggest":

| Content type | Model | Why |
|---|---|---|
| Quick exploration, casual speech, drafts | `base.en` (default) | ~80× realtime. Fine for "what's the gist" |
| Production captions, technical talks, multiple speakers | `large-v3` | Best accuracy on real speech |
| Mid-quality fast | `small.en` | Sweet spot for general English content |
| Music lyrics specifically | `medium.en` | Empirically beats `large-v3-turbo` on lyrics — the distillation regresses on hard musical input |
| Non-English speech | drop the `.en` (use `tiny`/`base`/`small`/`medium`/`large-v3`) | Multilingual variants required for auto-detect or non-English |

If you don't know what's in it: start with `base.en` to see the language and shape. Re-transcribe with the right model if you need precision.

### `extract_video` before `transcribe_audio`

Always try `extract_video(url, want_transcript=True)` first. Many YouTube videos have free auto-captions — you get the transcript with zero GPU cost. Only fall back to `transcribe_audio` when `extract_video` returns `transcript: None`.

---

## Workflow recipes

### Research a topic (general web)

```
1. search_web(query, max_results=8)
2. For top 3-5 results: fetch_page(url) (parallel if your runtime supports it)
3. Synthesize across the fetched pages
4. If a key source got blocked, retry that one with cf_browse(humanize=true)
```

### Dissect a YouTube video for content

```
1. extract_video(url, want_transcript=True)
2. If transcript is present: use it. Done.
3. If transcript is None or empty:
     transcribe_audio(url, model="large-v3")  # production-grade for spoken
   For music/lyrics:
     transcribe_audio(url, model="medium.en")
```

### Ingest a PDF the user shared

```
For structured data (tables, headings, references):
  convert_document(source)

For "just read it to me" plain text:
  pdf_extract_text(source)
```

### Scrape data from a bot-protected site

```
1. fetch_page(url) — try the cheap path first
2. If empty/blocked: cf_browse(url, humanize=true, captchaPolicy="detect")
3. If challenge detected, escalate to cf_browse with captchaPolicy="attempt"
   (returns challenge metadata + screenshot for analysis)
4. For multi-step flow: cf_browse_session_start → cf_browse_session_navigate/action/snapshot
```

### Capture and reason about a page visually

```
1. cf_browse_screenshot(url, humanize=true, fullPage=true)
2. Use your own vision capability on the returned image (or pass to a vision model if you don't have one)
3. Combine with cf_browse text content for cross-validation
```

---

## Gotchas worth knowing

- **First `cf_browse` call after a toolbox rebuild may fail** with "Version information not found at .../version.json". Camoufox is downloading its Firefox binary (~300 MB). Wait 60 seconds and retry — subsequent calls work.
- **Some pages crash Camoufox** with `unhandledRejection: TypeError`. Known cases: `bot.sannysoft.com`, occasionally `nowsecure.nl`. This is a camoufox-mcp v2.0.7 upstream bug. The toolbox auto-recovers (FastMCP proxy respawns the subprocess), but the failing call doesn't return — retry on a different URL or fall back to `fetch_page` if relevant.
- **`maxChars` minimum is 1000 on cf_browse**. The tool will validation-error if you pass less.
- **Camoufox sessions auto-expire after 10 minutes.** If you're doing a long multi-step flow, refresh with `cf_browse_session_resume` before it times out.
- **Whisper transcription of long audio is slow on `large-v3`.** ~8-12× realtime on the 5700 XT means a 30-min audio takes ~2.5-4 min. For quick previews use `small.en` first.
- **`transcribe_audio` accepts URLs as `source`** — it'll yt-dlp the audio for you. You don't need to download first.

---

## When NOT to use the toolbox

- Pages already trivially accessible to your built-in fetch (no JS, no auth, no bot detection) — your native fetch is fine.
- Local files you have direct read access to — read them directly rather than round-tripping.
- Image reasoning if you have native vision — describing/captioning is better with native vision than via any wrapper.

---

## Where this document should live (persistence)

The setup steps above (Before you start) are one-shot. Once the toolbox is wired into your runtime, this document is what helps you USE it well across future sessions — selection rules, recipes, gotchas. Don't make the user re-paste it every time. Persist it into your runtime's appropriate slot:

**Claude Code (skill):** save the body of this document at `~/.claude/skills/toolbox/SKILL.md` with this frontmatter:

```yaml
---
description: Using the homelab toolbox MCP — tool selection rules, workflow recipes, gotchas. Use when calling any mcp__toolbox__* tool or when planning research/scraping/transcription/PDF workflows.
---
```

**Hermes / agentic harness:** add this file to the agent's working-context load list (config option varies by harness).

**Custom local-model agent:** include this content in the system prompt or as a tool-selection guide loaded before the first user turn.

**No persistence slot available:** paste the **Capability map** and **Selection rules** sections into the system prompt. Those two are the smallest unit of useful guidance — everything else can be re-derived from them.

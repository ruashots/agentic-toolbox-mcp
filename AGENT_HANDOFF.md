# Using the toolbox

You have access to a shared MCP gateway that exposes web/doc/media/browser-automation tools. This document tells you which tool to pick when, how to compose them into common workflows, and which gotchas to avoid.

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

## How to install this document

Pick the wrapper that matches your agent runtime:

**Claude Code (skill):** add YAML frontmatter and drop in `~/.claude/skills/toolbox/SKILL.md`:

```yaml
---
description: Using the homelab toolbox MCP — tool selection rules, workflow recipes, gotchas. Use when calling any mcp__toolbox__* tool or when planning research/scraping/transcription/PDF workflows.
---
```

Followed by the body of this document.

**Hermes / agentic harness:** load this file into the agent's working context at session start (config option varies by harness).

**Custom local-model agent:** include this content in the system prompt or as a tool-selection guide loaded before the first user turn.

**Any agent:** if you can't auto-load it, paste the **Capability map** and **Selection rules** sections into the system prompt. Those are the smallest unit of useful guidance.

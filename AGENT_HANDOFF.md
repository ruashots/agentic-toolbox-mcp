# Using the toolbox

For AI agents. A shared MCP server, the toolbox, is (or is about to be) available on
the user's network. It gives you web search, page reading, a stealth browser,
document and PDF conversion, video tools, transcription and text-to-speech. This
document tells you how to connect yourself to it, which tool to pick when, how to
combine them, and what to watch out for.

---

## Before you start

Check your tool list. If `mcp__toolbox__*` tools are already there, you are set: skip
to **Capability map** and start working.

If they are missing, do not try to call them (you will only get `Unknown tool`), and
do not probe the network or guess. Ask the user one question first:

> "Where do we stand on the toolbox? I can handle three cases:
> 1. Nothing is set up: I install it from scratch.
> 2. It runs somewhere but I am not connected: give me the host or URL and I will connect.
> 3. I am connected but cannot see the tools: I will walk you through a refresh."

Then act on the answer.

### (1) Nothing is set up

You are going to install the toolbox. That is a real commitment on the user's
machine: a first build that downloads about 5.5 GB, about 48 GB of images, build
cache and models on disk afterwards, and seven containers that keep running. Confirm before you
start.

Ask the user: *"Should it run on this machine, or on another one (a home server, a
VM)?"* It needs an x86-64 machine with Docker and Docker Compose v2, at least 55 GB
of free disk and 8 GB of RAM. No GPU is needed.

Then, on the chosen machine:

```bash
git clone https://github.com/Ruashots/agentic-toolbox-mcp.git
cd agentic-toolbox-mcp
docker compose up -d --build
```

Container logs are off by design, so do not wait on `docker compose logs`. When the
build has finished, wait until the watcher reports its first checks:

```bash
docker logs tb-watch
```

Lines such as `[watch] ping: ok` and `[watch] search_web: ok` mean the toolbox works.
Find the machine's address on the network (`hostname -I`, or the name the user knows
it by) and continue with step (2).

If the user's machine is a poor place for a service that runs all the time (a laptop
that sleeps, a sandbox), say so honestly and ask which machine should run it.

### (2) It runs somewhere, but you are not connected

Ask the user: *"What is the toolbox URL?"* It usually looks like
`http://<host>:8000/mcp`.

Then:

1. **Check that it answers.** `curl http://<host>:8000/mcp` should come back with a
   short JSON error that says `Client must accept text/event-stream`. That error is
   the expected answer: the endpoint is up. If the connection is refused or times out,
   the toolbox is not running where the user thinks; go back to (1).
2. **Connect.** The toolbox has no login.
   - Claude Code: `claude mcp add --transport http --scope user toolbox <url>`
   - If the user put the toolbox behind a proxy that needs a header, add it:
     `claude mcp add --transport http --scope user toolbox <url> --header "Authorization: Bearer <token>"`
   - Other MCP clients: a streamable HTTP server at `<url>`. Where that setting lives
     depends on your runtime; ask the user if you do not know.
3. **Reload.** In Claude Code, the new tools appear in a new session. Other runtimes
   differ.
4. **Check with `ping`.** It returns `"pong"`. If it does not, go to (3).

### (3) Connected, but you cannot see the tools

Your session probably listed its tools before the toolbox was reachable.

- Claude Code: start a new session. Inside a session, `/mcp` shows whether the
  toolbox server is connected.
- Other runtimes: reconnect the MCP client. How depends on the runtime.
- Then `ping` should return `"pong"`. If it still fails, the toolbox is not reachable
  from where you run: go back to (2) and confirm the URL with the user.

Once `ping` works, you have every tool. Carry on.

---

## Capability map (pick by intent)

| You need to... | Use | Notes |
|---|---|---|
| Search the web for current information | `search_web` | Meta-search over several engines. Returns title, url, snippet. An empty list means the query really had no hits: if the engines themselves fail, the tool raises an error and names them. |
| Read a normal web page | `fetch_page` | Rendered in a real browser by Crawl4AI, returned as clean markdown. The default for "give me what is at this URL". |
| Read a page that blocks normal browsers | `cf_browse` | Camoufox, a stealth Firefox. Use it when `fetch_page` returns almost nothing, a challenge page, or "checking your browser". See **Selection rules**. |
| Drive a page through several steps (click, fill, navigate) | `cf_browse_session_start`, then `cf_browse_session_navigate` / `_action` / `_snapshot`, then `cf_browse_session_close` | A browser session that keeps its state between calls. For "click load more, then scrape" or login flows. |
| Take a screenshot of a page | `cf_browse_screenshot` | Returns an image. Pair it with your own vision. |
| Get one kind of thing from a page | `cf_browse_links` / `cf_browse_forms` / `cf_browse_outline` / `cf_browse_find` | Cheaper than the full content when you only need links, forms, headings or a text match. |
| Convert a document to markdown | `convert_document` | URL or file path. PDF, Word (.docx), PowerPoint (.pptx), Excel (.xlsx, .xls), HTML, CSV, EPUB. **For structured markdown from a PDF, use this.** No OCR: images and scanned pages give an error that says no text was found. |
| Get plain text from a PDF | `pdf_extract_text` | Cleaner word spacing than `convert_document` on PDFs with columns. Use it for "just give me the readable text". |
| Get metadata and captions for a video | `extract_video` | yt-dlp, works with YouTube and many other sites. Returns title, uploader, duration, description and the English auto-captions when they exist. **Try this first** before transcribing: many videos have free captions. |
| Get the audio or video **file** itself | `download_media` | Takes the best stream as is, with no re-encoding, and returns it as base64 (`content_b64`): decode and save it. `kind="audio"` or `"video"`. Files over `max_mb` (default 25) are refused. Use it when you need the media, not its text. |
| Transcribe audio or video that has no captions | `transcribe_audio` | Local Whisper, on the toolbox's GPU if it has one, otherwise on its CPU. See **Model selection**: bigger is not always better. |
| Turn text into speech | `synthesize_speech` | CPU only, slower than real time. Split long scripts into paragraphs or sentences. See **Text to speech** below. |
| See which tools work right now | `toolbox_status` | Each tool's latest scheduled check: up, down (a known outage) or not checked yet. |
| Check the toolbox is reachable | `ping` | Returns `"pong"`. |

---

## Selection rules

### `fetch_page` or `cf_browse`

Try `fetch_page` first. It is faster and lighter, and it handles most pages,
including most JavaScript-heavy ones.

Switch to `cf_browse` when you see any of:

- The answer is suspiciously short (under 200 characters) or only a page skeleton.
- The title or text says "Just a moment", "Checking your browser", "Please verify",
  "Access denied" or "Bot detected".
- The site is known to use Cloudflare Turnstile, DataDome, PerimeterX or Akamai Bot
  Manager.
- Crawl4AI returns an error that mentions a challenge or a 403.

Realistic mouse movement and human-like timing (`humanize`) are on by default; keep
them on for hostile sites.

### `cf_browse` or `cf_browse_session_*`

One page to read: `cf_browse`. It starts a browser for the call and closes it after.

Several steps: start a session, do the steps, close it. The session keeps cookies,
login state and the viewport between calls. **Close it with
`cf_browse_session_close` as soon as you are done:** while a session is open it holds
the browser, and every other browser call (yours or another agent's) fails after
waiting 30 seconds.

### `convert_document` or `pdf_extract_text`

Both read PDFs, with different strengths:

- `convert_document` (MarkItDown) keeps structure: tables become markdown tables and
  headings stay headings. **Quirk:** PDFs with many columns sometimes lose the spaces
  between words ("AttentionIsAllYouNeed").
- `pdf_extract_text` (Stirling-PDF) keeps clean word spacing and no structure. Plain
  text only.

Pick by what you need: structure, `convert_document`; readable text, `pdf_extract_text`.

### `transcribe_audio` model selection

Models download the first time they are used, then stay cached. Pick by the content,
not "always the biggest":

| Content | Model | Why |
|---|---|---|
| A quick look, casual speech, drafts | `base.en` (default) | Fast. Fine for "what is this about". |
| Captions people will read, technical talks, several speakers | `large-v3` | The most accurate on real speech. |
| Good quality, still quick | `small.en` | The sweet spot for general English. |
| Song lyrics | `medium.en` | In practice it beats `large-v3-turbo` on lyrics. |
| Not English | a model without `.en` (`tiny`, `base`, `small`, `medium`, `large-v3`), with `language` set (`es`, `fr`, ...) | Without `language`, Whisper assumes English. |

If you do not know the language, use a model without `.en` and `language="auto"`: the
answer's `language` field says what was detected. Then transcribe again with the
right model if you need precision.

Speed depends on the toolbox's hardware. On an AMD Radeon RX 5700 XT, `base.en` runs
about 80 times faster than real time and `large-v3` about 8 to 12 times. On a CPU,
`base.en` runs about 10 times faster than real time, `small.en` about 4 times and
`large-v3-turbo` about 1.5 times; `large-v3` is slower still. If the toolbox runs on
a CPU (ask the user, or notice slow transcriptions), prefer `base.en` or `small.en`
unless the user needs the best accuracy.

### `extract_video` before `transcribe_audio`

Always try `extract_video(url, want_transcript=True)` first. Many YouTube videos have
free auto-captions, so you get the transcript without any transcription work. Only
fall back to `transcribe_audio` when `extract_video` returns `transcript: None`.

### Text to speech

Three rules:

1. **Split long scripts into paragraphs or sentences.** Each call adds only about a
   second, and long texts get slower per second of audio: 39 seconds of speech took
   186 s as one call and 104 s as seven sentence calls. With a cloned voice the
   difference is small (one call was a little faster), so do what suits the job.
2. **Always pass `reference_text` with `voice_reference`.** Without it the model first
   transcribes the reference clip, which on a CPU costs more than the speech you
   wanted (and the first time, downloads a 1.6 GB Whisper model to do it).
3. **`steps=16` when the words matter more than the delivery** (drafts, checking
   pronunciation). It is roughly twice as fast. Keep the default 32 for anything a
   person will listen to.

It is slower than real time: budget about 3 to 5 times the length of the audio at
the default quality, depending on the toolbox's CPU, and about half that with
`steps=16`. Tell the user before you start a long job, not after. For anything
interactive or streaming, it is the wrong tool.

To clone a voice, use a 3 to 10 second clip in the same language as the text. Longer
clips do not help and can hurt. A clip in another language works, but its accent
carries over.

---

## Workflow recipes

### Research a topic on the web

```
1. search_web(query, max_results=8)
2. fetch_page(url) for the top 3 to 5 results (in parallel if your runtime can)
3. Combine what the pages say
4. If a key source was blocked, retry that one with cf_browse(url)
```

### Take apart a YouTube video

```
1. extract_video(url, want_transcript=True)
2. If there is a transcript: use it. Done.
3. If transcript is None or empty:
     transcribe_audio(url, model="large-v3")   # for speech people will read
   For music and lyrics:
     transcribe_audio(url, model="medium.en")
```

### Read a PDF the user shared

```
For structure (tables, headings, references):
  convert_document(source)

For "just read it to me" plain text:
  pdf_extract_text(source)
```

### Get data from a bot-protected site

```
1. fetch_page(url): try the cheap path first
2. If empty or blocked: cf_browse(url, captchaPolicy="detect")
3. If a challenge is detected: cf_browse with captchaPolicy="attempt"
   (returns details about the challenge and a screenshot to look at)
4. For a flow with several steps: cf_browse_session_start, then
   cf_browse_session_navigate / _action / _snapshot
```

### Look at a page

```
1. cf_browse_screenshot(url, fullPage=true)
2. Use your own vision on the image (or pass it to a vision model)
3. Check what you see against the text from cf_browse
```

---

## Things to know

- **When a tool fails, read the end of the error.** The toolbox adds a line saying
  whether the tool is a known outage (retrying will not help; use the alternative it
  names), failed its latest scheduled check (it may be breaking), or passed it (one
  retry is reasonable). `toolbox_status` lists every tool's state. Do not loop on
  retries.
- **Some pages crash the stealth browser** with `unhandledRejection: TypeError`. Known
  cases: `bot.sannysoft.com`, and sometimes `nowsecure.nl`. This is a bug in
  camoufox-mcp 2.0.7. The toolbox notices within a minute and restarts itself, which
  takes about 10 seconds, but the call that crashed it does not return. Try another
  URL, or `fetch_page` if it fits.
- **The browser handles one page at a time.** Up to 8 more browser calls wait in a
  queue for up to 30 seconds each; beyond that a call fails with "Too many concurrent
  browse requests" or "Timed out waiting for a browse slot", and the note at the end
  says the browser is busy, not broken. Only one multi-step session can be open at a
  time, and while it is open no other browser call runs.
- **`maxChars` is at least 1000 on `cf_browse`.** Smaller values fail validation.
- **Browser sessions close after 10 minutes without a call.** Every session call
  resets that timer.
- **The browser tools refuse private and local addresses**, such as `192.168.x.x` or
  `localhost`. The other tools do not.
- **`transcribe_audio` takes URLs as `source`.** It downloads the audio itself; you do
  not need to download first.
- **`convert_document` does not transcribe audio or read images.** It tells you so
  with an error. Use `transcribe_audio` for audio and video.

---

## When not to use the toolbox

- Pages your own fetch tool already reads fine (no JavaScript, no login, no bot
  protection): your own tool is fine.
- Local files you can read directly: read them yourself instead of sending them
  through the toolbox.
- Describing images when you have vision yourself: your own vision does better than
  any wrapper.

---

## Where to keep this document

The setup steps above are for the first time. After that, this document helps you
*use* the toolbox well in later sessions: selection rules, recipes, things to know.
Keep it where your runtime loads it, so the user does not have to paste it again:

**Claude Code (skill):** save this document as `~/.claude/skills/toolbox/SKILL.md`,
with this frontmatter at the top:

```yaml
---
name: toolbox
description: Using the Agentic Toolbox MCP server. Which tool to pick, workflow recipes, things to know. Use when calling any mcp__toolbox__* tool or planning research, scraping, transcription, PDF or speech work.
---
```

**Other agent harnesses:** add this file to the agent's context files. The setting
depends on the harness.

**Your own local-model agent:** put this content in the system prompt, or load it as
a tool guide before the first user turn.

**No place to keep it:** put the **Capability map** and **Selection rules** sections
in the system prompt. They are the smallest useful part; the rest follows from them.

"""toolbox-gateway: one MCP endpoint for web, document, media and browser tools.

Adding a new tool:
1. If it needs a new container, add it to ../docker-compose.yml on the same `toolbox` network.
2. If it needs a Python package, add it at an exact version to requirements.lock.
3. Add an @mcp.tool function below, a real-job check for it to CHECKS in watch.py,
   and what to use instead while it is down to _ALTERNATIVES.
4. Rebuild + restart: `docker compose build gateway && docker compose up -d --no-deps gateway`

Exposed at: http://<host>:8000/mcp (streamable HTTP transport)
"""

import base64
import json
import os
import re
import subprocess
import tempfile
from pathlib import Path

import httpx
from fastmcp import FastMCP
from fastmcp.server import create_proxy

# Expire MCP sessions idle for 15 min. FastMCP 3.4.4 does not expose the MCP SDK's
# session_idle_timeout, and without it every session a client opens and never
# DELETEs lives forever: a client opening one every few seconds grew this process
# to ~11 GB in ~2 days, the host thrashed for hours (connect timeouts), then OOM.
# Claude Code re-initializes transparently on the resulting 404 (tested 2026-10-02).
import fastmcp.server.http as _fastmcp_http
_orig_session_manager_init = _fastmcp_http.FastMCPStreamableHTTPSessionManager.__init__
def _session_manager_init_with_idle_timeout(self, *args, **kwargs):
    _orig_session_manager_init(self, *args, **kwargs)
    if not self.stateless:
        self.session_idle_timeout = 900
_fastmcp_http.FastMCPStreamableHTTPSessionManager.__init__ = _session_manager_init_with_idle_timeout
from markitdown import MarkItDown

SEARXNG_URL = os.environ.get("SEARXNG_URL", "http://searxng:8080")
CRAWL4AI_URL = os.environ.get("CRAWL4AI_URL", "http://crawl4ai:11235")
STIRLING_URL = os.environ.get("STIRLING_URL", "http://stirling-pdf:8080")
WHISPER_URL = os.environ.get("WHISPER_URL", "http://whisper:8000")
TTS_URL = os.environ.get("TTS_URL", "http://tts:8000")

mcp = FastMCP(
    "toolbox",
    instructions=(
        "Toolbox: search the web, read JavaScript-rendered pages, drive a stealth "
        "browser, convert documents and PDFs to text, get video metadata, "
        "transcripts and media files, transcribe audio, and synthesise speech "
        "(with optional voice cloning). "
        "Prefer these tools over training-knowledge guesses when "
        "the user needs fresh, specific, or fully-rendered content. "
        "A watcher checks the tools with real jobs on a schedule (one browser "
        "check covers all cf_* tools). If a tool fails, its error says whether it "
        "is a known outage, since when, and which tool to use instead; call "
        "toolbox_status to see the state of every tool before retrying."
    ),
)

md_converter = MarkItDown()


def _backend_error(r: httpx.Response) -> str:
    """The reason a backing service gave for an error, instead of a bare status line."""
    reason = None
    try:
        body = r.json()
        if isinstance(body, dict):
            reason = body.get("detail") or body.get("message") or body.get("error")
    except Exception:
        pass
    return f"{r.status_code}: {reason or r.text.strip()[:500] or r.reason_phrase}"


class SourceError(RuntimeError):
    """A URL or file the caller gave could not be read. Not a toolbox fault."""


def _download(url: str, timeout: float) -> httpx.Response:
    """Fetch a URL the caller gave. Errors name the URL and do not blame the toolbox."""
    try:
        r = httpx.get(url, timeout=timeout, follow_redirects=True)
        r.raise_for_status()
    except httpx.HTTPError as exc:
        raise SourceError(f"could not download {url}: {exc}") from exc
    return r


def _crawl_error(r: httpx.Response) -> str:
    """Crawl4AI wraps the browser's error in its own traceback text; keep what happened."""
    reason = _backend_error(r).split("\n\nCode context:")[0]
    for pattern in (r"net::ERR_\w+ at \S+", r"Blocked by anti-bot protection[^\n]*"):
        m = re.search(pattern, reason)
        if m:
            return m.group(0)
    return reason[:600]


# ---------------------------------------------------------------------------
# Health: what the watcher (tb-watch, watch.py) last saw, told to agents.
# ---------------------------------------------------------------------------
STATUS_FILE = Path(os.environ.get("STATUS_FILE", "/health/status.json"))
WATCHER_STALE_S = 900

# For each check: what an agent should use instead while it is failing.
_ALTERNATIVES = {
    "search_web": "No other search tool here. As a fallback, fetch_page a search results page "
                  "(e.g. https://html.duckduckgo.com/html/?q=your+terms).",
    "fetch_page": "Use cf_browse (Camoufox browser) for the same URL.",
    "cf_browse": "Use fetch_page (Crawl4AI) for ordinary pages; it has no stealth for bot-protected sites.",
    "convert_document": "For PDFs, use pdf_extract_text.",
    "pdf_extract_text": "Use convert_document, which also reads PDFs.",
    "transcribe_audio": "For YouTube and similar sites, extract_video(want_transcript=True) returns the "
                        "site's own captions when they exist.",
    "synthesize_speech": "No other speech tool here.",
    "extract_video": "transcribe_audio downloads with the same tool (yt-dlp), so it may fail on the same "
                     "sites; for the page's text, use fetch_page on the URL.",
    "download_media": "No other download tool here.",
    "ping": "The toolbox endpoint itself is failing; every tool is affected.",
}


def _check_for(tool: str) -> str:
    return "cf_browse" if tool.startswith("cf_") else tool


def _read_status() -> dict | None:
    try:
        return json.loads(STATUS_FILE.read_text())
    except Exception:
        return None


def _status_age_s(status: dict) -> float | None:
    from datetime import datetime, timezone
    try:
        return (datetime.now(timezone.utc) - datetime.fromisoformat(status["updated"])).total_seconds()
    except Exception:
        return None


def _health_note(tool: str, error_text: str = "") -> str:
    """One paragraph for an agent whose call just failed: is this a known outage, what now."""
    check = _check_for(tool)
    if check == "cf_browse" and any(m in error_text for m in BROWSER_BUSY_MARKERS):
        return ("[toolbox] The browser is busy, not broken. It handles one page at a time, and an "
                "open multi-step session (cf_browse_session_*) holds it until the session is closed "
                "or has had no call for 10 minutes. Try again shortly; if you opened a session, "
                "close it first.")
    status = _read_status()
    alt = _ALTERNATIVES.get(check, "")
    if not status:
        return f"[toolbox] No health data available (the watcher is not running). {alt}".strip()
    age = _status_age_s(status)
    stale = age is None or age > WATCHER_STALE_S
    t = status.get("tools", {}).get(check)
    if not t:
        return f"[toolbox] {alt}" if alt else ""
    subject = "This tool" if check == tool else "The browser that all cf_* tools share"
    if t.get("status") == "down":
        since = (t.get("since") or "?")[:16].replace("T", " ")
        msg = (f"[toolbox] KNOWN OUTAGE: {t.get('description', check)} has been failing since {since} UTC. "
               f"Retrying will not help until it is fixed. ")
    elif t.get("consecutive_failures"):
        msg = (f"[toolbox] {subject} failed its most recent scheduled check and is being checked again, "
               f"so it may be breaking. ")
    elif t.get("status") == "up":
        last_ok = (t.get("last_ok") or "never")[:16].replace("T", " ")
        msg = (f"[toolbox] {subject} passed its last scheduled real-job check (last success {last_ok} UTC), "
               f"so this failure may be specific to your input or transient. One retry is reasonable. ")
    else:
        msg = (f"[toolbox] {subject} has not been checked yet (the toolbox started recently), "
               f"so this failure may be specific to your input or transient. One retry is reasonable. ")
    if stale:
        msg += "(Health data is stale: the watcher has not reported recently.) "
    return (msg + alt).strip()


from fastmcp.exceptions import ToolError
from fastmcp.server.middleware import Middleware

from watch import BROWSER_BUSY_MARKERS


class _ExplainFailures(Middleware):
    """Append the health note to every failing tool call, so an agent learns at once
    whether a tool is known broken and what to use instead, rather than retrying blind."""

    async def on_call_tool(self, context, call_next):
        tool = getattr(context.message, "name", "") or ""
        try:
            result = await call_next(context)
            # Proxied tools (the cf_* browser tools) report failure as an error result
            # instead of raising. Add the same note to those.
            if getattr(result, "is_error", False) and tool != "toolbox_status":
                text = "\n".join(getattr(c, "text", "") or "" for c in result.content or [])
                note = _health_note(tool, text)
                if note:
                    from mcp.types import TextContent
                    result.content = list(result.content or []) + [
                        TextContent(type="text", text="\n\n" + note)
                    ]
            return result
        except Exception as e:
            if tool == "toolbox_status":
                raise
            # Blame the toolbox only when one of its own services could not be reached,
            # not when the caller's URL could not be (SourceError).
            cause = e
            while cause is not None and not isinstance(cause, (httpx.ConnectError, SourceError)):
                cause = cause.__cause__ or cause.__context__
            backend_down = isinstance(cause, httpx.ConnectError)
            prefix = "The service behind this tool is not reachable. " if backend_down else ""
            note = _health_note(tool, str(e))
            raise ToolError(f"{prefix}{e}" + (f"\n\n{note}" if note else "")) from e


mcp.add_middleware(_ExplainFailures())


@mcp.tool
def toolbox_status() -> dict:
    """State of every toolbox tool, from scheduled checks that run one real job per tool.

    Call this when a tool fails, or before relying on a tool for a long task.
    status is "up", "down" (known outage, retrying will not help) or "unknown"
    (not checked yet). All cf_* browser tools share one browser and are covered
    by the cf_browse check.
    """
    status = _read_status()
    if not status:
        return {"watcher": "not running: no health data", "tools": {}}
    age = _status_age_s(status)
    out = {}
    for name, t in status.get("tools", {}).items():
        out[name] = {
            "what": t.get("description"),
            "status": t.get("status"),
            "since": t.get("since"),
            "last_check": t.get("last_check"),
            "last_ok": t.get("last_ok"),
            "checked_every_s": t.get("interval_s"),
        }
        if t.get("status") != "up":
            out[name]["last_error"] = t.get("last_error")
            out[name]["use_instead"] = _ALTERNATIVES.get(name)
    return {
        "watcher": "ok" if age is not None and age <= WATCHER_STALE_S else f"stale: last report {age and int(age)} s ago",
        "updated": status.get("updated"),
        "tools": out,
    }


@mcp.tool
def search_web(query: str, max_results: int = 10) -> list[dict]:
    """Search the web via SearXNG (meta-search across several search engines).

    An empty list always means the query genuinely had no hits. If the backend
    answers with nothing because its engines refused, this raises instead.

    Args:
        query: search terms
        max_results: the most results to return, 1 to 30 (default 10). This is one page
            of results from the engines, often 10 to 20, so you may get fewer.
    """
    if not 1 <= max_results <= 30:
        raise ValueError(f"max_results must be between 1 and 30, not {max_results}")
    r = httpx.get(
        f"{SEARXNG_URL}/search",
        params={"q": query, "format": "json"},
        timeout=20,
    )
    if r.is_error:
        raise RuntimeError(f"search failed: {_backend_error(r)}")
    payload = r.json()
    results = payload.get("results", [])
    # SearXNG answers 200 with an empty list in two very different cases: the
    # query has no hits, or every engine it tried refused (CAPTCHA, rate limit,
    # access denied). Returning [] for the second one reads as "no results" and
    # sends the caller down the wrong path, so name the failures instead.
    if not results:
        dead = payload.get("unresponsive_engines") or []
        if dead:
            detail = "; ".join(f"{name}: {reason}" for name, reason in dead)
            raise RuntimeError(
                f"SearXNG returned nothing for {query!r} and these engines failed: "
                f"{detail}. This is a backend fault, not an empty result set."
            )
    return [
        {
            "title": x.get("title", ""),
            "url": x.get("url", ""),
            "snippet": x.get("content", "")[:300],
            "engine": x.get("engine", ""),
        }
        for x in results[:max_results]
    ]


@mcp.tool
def fetch_page(url: str, render_js: bool = True) -> str:
    """Fetch a web page and return clean markdown.

    Uses Crawl4AI, which loads the page in a real browser (Playwright), so pages
    built with JavaScript (SPAs, React/Next/Vue) come back as their rendered text.
    Sites with bot protection may return a challenge page instead: use cf_browse.

    Args:
        url: fully-qualified URL
        render_js: kept for compatibility and ignored: pages are always rendered in a browser.
    """
    r = httpx.post(
        f"{CRAWL4AI_URL}/md",
        json={"url": url, "f": "fit"},  # fit = readability-style extraction
        timeout=90,
    )
    if r.is_error:
        raise RuntimeError(f"could not fetch {url}: {_crawl_error(r)}")
    body = r.json()
    return body.get("markdown", "") or body.get("html", "")


@mcp.tool
def convert_document(source: str) -> str:
    """Convert a document (PDF, Word .docx, PowerPoint .pptx, Excel .xlsx/.xls, HTML, CSV, EPUB) to markdown.

    There is no OCR: text inside images and scanned pages is not read. It does not
    transcribe audio or video either (use transcribe_audio for that). When nothing
    readable is found, it raises an error instead of returning an empty string.

    Args:
        source: URL or local path. Local paths must be reachable inside the gateway container.
    """
    if source.startswith(("http://", "https://")):
        r = _download(source, timeout=60)
        suffix = Path(source.split("?")[0]).suffix or ".bin"
        with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as f:
            f.write(r.content)
            path = f.name
        try:
            text = md_converter.convert(path).text_content
        finally:
            os.unlink(path)
    else:
        text = md_converter.convert(source).text_content
    if not text.strip():
        raise RuntimeError(
            f"No text found in {source}. convert_document reads the text of documents. It has no "
            "OCR, so images and scanned pages come back empty, and it does not transcribe audio "
            "or video (use transcribe_audio for that)."
        )
    return text


@mcp.tool
def extract_video(url: str, want_transcript: bool = True) -> dict:
    """Extract metadata (and optionally auto-transcript) from a YouTube/podcast/etc URL.

    Args:
        url: any URL supported by yt-dlp (1000+ sites)
        want_transcript: pull auto-generated subtitles if available (English)
    """
    with tempfile.TemporaryDirectory() as tmp:
        cmd = [
            "yt-dlp",
            "--skip-download",
            "--print-json",
            "--no-warnings",
        ]
        if want_transcript:
            cmd += [
                "--write-auto-sub",
                "--sub-lang", "en",
                "--sub-format", "vtt",
                "-o", f"{tmp}/%(id)s.%(ext)s",
            ]
        cmd.append(url)
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
        if result.returncode != 0:
            raise RuntimeError(f"yt-dlp failed: {result.stderr.strip()}")
        meta = json.loads(result.stdout.splitlines()[0])
        out = {
            "title": meta.get("title"),
            "uploader": meta.get("uploader"),
            "duration_seconds": meta.get("duration"),
            "description": (meta.get("description") or "")[:1000],
            "url": meta.get("webpage_url"),
        }
        if want_transcript:
            vtt_files = list(Path(tmp).glob("*.vtt"))
            out["transcript"] = vtt_files[0].read_text() if vtt_files else None
        return out


@mcp.tool
def download_media(url: str, kind: str = "audio", quality: str = "best", max_mb: int = 25) -> dict:
    """Download the actual media FILE (audio or video) from a URL and return it base64-encoded.

    Unlike extract_video (returns metadata/transcript) and transcribe_audio (returns text),
    this hands back the real file bytes so the caller can save/use it. Uses yt-dlp with the
    container's Node JS runtime, so it sees the full format list and grabs the genuine best
    stream, NOT YouTube's low-quality `format 18` fallback you get without a JS runtime. The
    stream is taken as-is (no re-encode), so audio stays pristine.

    Args:
        url: any yt-dlp-supported URL (1000+ sites).
        kind: "audio" (default) or "video".
        quality: "best" (default) or an explicit yt-dlp `-f` format selector to override.
        max_mb: refuse files larger than this (the bytes ride in the response). Raise for big video.

    Returns: {"filename", "ext", "format", "size_bytes", "content_b64"}. Decode content_b64 to get the file.
    """
    import base64

    if quality and quality != "best":
        fmt = quality
    elif kind == "video":
        fmt = "bestvideo*+bestaudio/best"
    else:  # audio: prefer m4a, never force a re-encode
        fmt = "bestaudio[ext=m4a]/bestaudio/best"

    with tempfile.TemporaryDirectory() as tmp:
        cmd = [
            "yt-dlp", "--js-runtimes", "node", "--no-warnings",
            "-f", fmt, "-o", f"{tmp}/media.%(ext)s", url,
        ]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
        if result.returncode != 0:
            raise RuntimeError(f"yt-dlp failed: {result.stderr.strip()[:500]}")
        files = [p for p in Path(tmp).iterdir() if p.is_file()]
        if not files:
            raise RuntimeError(f"no media file produced for {url}")
        f = max(files, key=lambda p: p.stat().st_size)
        size = f.stat().st_size
        if size > max_mb * 1024 * 1024:
            raise RuntimeError(
                f"file is {size // (1024*1024)}MB, over max_mb={max_mb}. Raise max_mb to allow it."
            )
        return {
            "filename": f.name,
            "ext": f.suffix.lstrip("."),
            "format": fmt,
            "size_bytes": size,
            "content_b64": base64.b64encode(f.read_bytes()).decode(),
        }


@mcp.tool
def pdf_extract_text(source: str) -> str:
    """Extract plain text from a PDF using Stirling-PDF's text extractor.

    Args:
        source: URL or local path to a PDF
    """
    if source.startswith(("http://", "https://")):
        pdf_bytes = _download(source, timeout=60).content
    else:
        pdf_bytes = Path(source).read_bytes()

    files = {"fileInput": ("doc.pdf", pdf_bytes, "application/pdf")}
    r = httpx.post(
        f"{STIRLING_URL}/api/v1/convert/pdf/text",
        files=files,
        data={"outputFormat": "txt"},
        timeout=120,
    )
    if r.is_error:
        raise RuntimeError(f"PDF text extraction failed: {_backend_error(r)}")
    return r.text


@mcp.tool
def ping() -> str:
    """Health check. Returns 'pong'. Use to verify the toolbox is reachable."""
    return "pong"


@mcp.tool
def transcribe_audio(
    source: str,
    model: str = "base.en",
    language: str | None = None,
) -> dict:
    """Transcribe audio/video to text with local Whisper (whisper.cpp), on the toolbox's GPU if it has one, otherwise its CPU.

    Use for YouTube videos without auto-captions, podcasts, recorded meetings, or any audio file.
    For URLs, audio is downloaded via yt-dlp (1000+ sites supported) and normalized before transcription.

    Args:
        source: URL (yt-dlp supports YouTube/Vimeo/podcast feeds/etc) or local path.
        model: whisper model. English-only: tiny.en/base.en/small.en/medium.en (default base.en).
               Multilingual: tiny/base/small/medium/large-v3/large-v3-turbo. Bigger = more accurate,
               slower, larger first download. Measured speed, in times faster than real time:
               on a CPU, base.en ~10x, small.en ~4x, large-v3-turbo ~1.5x; on an AMD RX 5700 XT,
               base.en ~80x and large-v3 ~8-12x. For the best accuracy use large-v3 or large-v3-turbo.
        language: optional ISO 639-1 code (en, es, etc). If omitted, Whisper assumes English.
                  Pass "auto" to have it detect the language (use a model without .en).

    Returns:
        {text: full transcript, model, language, segments: [{start, end, text}, ...]}
    """
    import shutil

    with tempfile.TemporaryDirectory() as tmp_dir:
        tmp = Path(tmp_dir)
        audio_path = tmp / "audio.m4a"

        if source.startswith(("http://", "https://")):
            # yt-dlp handles direct URLs too; let it try first.
            result = subprocess.run(
                [
                    "yt-dlp",
                    "-x", "--audio-format", "m4a",
                    "--no-warnings",
                    "-o", str(audio_path),
                    source,
                ],
                capture_output=True, text=True, timeout=600,
            )
            if result.returncode != 0:
                # Fall back to direct HTTP for non-yt-dlp URLs. If that only finds a web page
                # (or nothing), yt-dlp's own error is the useful one, so pass it on.
                ytdlp_error = result.stderr.strip()[-500:]
                try:
                    r = httpx.get(source, timeout=120, follow_redirects=True)
                    r.raise_for_status()
                except httpx.HTTPError as exc:
                    raise SourceError(f"could not get audio from {source}. yt-dlp said: {ytdlp_error}") from exc
                if r.headers.get("content-type", "").startswith(("text/", "application/json")):
                    raise SourceError(f"could not get audio from {source}. yt-dlp said: {ytdlp_error}")
                audio_path.write_bytes(r.content)
        else:
            shutil.copy(source, audio_path)

        if not audio_path.exists() or audio_path.stat().st_size == 0:
            raise RuntimeError(f"could not obtain audio from {source}")

        with open(audio_path, "rb") as f:
            files = {"file": ("audio.m4a", f, "audio/m4a")}
            data: dict = {"model": model}
            if language:
                data["language"] = language
            r = httpx.post(
                f"{WHISPER_URL}/transcribe",
                files=files, data=data,
                timeout=3600,
            )
        if r.is_error:
            raise RuntimeError(f"transcription failed: {_backend_error(r)}")
        return r.json()


@mcp.tool
def synthesize_speech(
    text: str,
    voice_reference: str | None = None,
    reference_text: str | None = None,
    steps: int = 32,
    normalize_numbers: bool = False,
) -> dict:
    """Turn text into speech (OmniVoice, 600+ languages, optional voice cloning).

    Runs on CPU, so it is slower than real time: budget about 3 to 5 times the audio
    length at the default quality, depending on the CPU, and about half that at
    steps=16. That is fine for narration, voiceover and audiobook-style batches; it is
    not suitable for interactive or streaming use.

    Each call adds only about a second, and long texts get slower per second of audio,
    so split a long script into paragraphs or sentences: 39 s of speech took 186 s in
    one call and 104 s in seven sentence calls. With a cloned voice, one call was a
    little faster (103 s against 125 s).

    Args:
        text: what to say. Any of the model's 600+ languages; it is inferred from the text.
        voice_reference: optional 3-10 second sample of a voice to clone. Accepts an
            http(s) URL or base64 (raw or `data:` URL). Omit for the model's default voice.
            Use a reference in the same language as `text`: a mismatch carries the
            reference language's accent into the output.
        reference_text: transcript of `voice_reference`. Optional but recommended: without
            it the model runs Whisper to transcribe the clip, which on CPU costs more than
            the synthesis itself.
        steps: diffusion steps, 1-64. 32 (default) for quality, 16 for roughly double speed.
        normalize_numbers: write numbers, dates and amounts out as words before speaking,
            for English and Chinese text ("I have 123 apples" -> "I have one hundred and
            twenty three apples", "25本书" -> "二十五本书"). Leave it off for other languages
            (their numbers would be read in English), for phone numbers and codes (they can
            come out wrong), and when the text already spells numbers out.

    Returns:
        {audio_b64, format: "wav", sample_rate, audio_seconds, generate_seconds,
         realtime_factor, cloned}. Decode `audio_b64` and write it to a .wav file.
    """
    files: dict = {}
    tmp_ref = None

    if voice_reference:
        if voice_reference.startswith(("http://", "https://")):
            ref_bytes = _download(voice_reference, timeout=120).content
        else:
            payload = voice_reference
            if payload.startswith("data:"):
                payload = payload.split(",", 1)[-1]
            try:
                ref_bytes = base64.b64decode(payload, validate=True)
            except Exception as exc:
                raise RuntimeError(
                    "voice_reference must be an http(s) URL or base64 audio"
                ) from exc
        if not ref_bytes:
            raise RuntimeError("voice_reference resolved to empty audio")
        tmp_ref = ref_bytes
        files["reference"] = ("reference.wav", tmp_ref, "audio/wav")

    data: dict = {
        "text": text,
        "steps": str(steps),
        "normalize_text": str(bool(normalize_numbers)).lower(),
    }
    if reference_text:
        data["reference_text"] = reference_text

    r = httpx.post(
        f"{TTS_URL}/synthesize",
        data=data,
        files=files or None,
        timeout=3600,
    )
    if r.is_error:
        raise RuntimeError(f"speech synthesis failed: {_backend_error(r)}")

    return {
        "audio_b64": base64.b64encode(r.content).decode(),
        "format": "wav",
        "sample_rate": int(r.headers.get("X-Sample-Rate", 24000)),
        "audio_seconds": float(r.headers.get("X-Audio-Seconds", 0)),
        "generate_seconds": float(r.headers.get("X-Generate-Seconds", 0)),
        "realtime_factor": float(r.headers.get("X-Realtime-Factor", 0)),
        "cloned": r.headers.get("X-Cloned") == "1",
    }


# Mount whit3rabbit/camoufox-mcp's 17 browser-automation tools into our surface.
# Camoufox is a privacy-focused Firefox fork with C++-level anti-fingerprinting,
# so this is what we use whenever Crawl4AI gets caught by bot-detection (Cloudflare,
# DataDome, etc) or we need true multi-step browser automation.
# Tools surface as `mcp__toolbox__cf_<name>`. The browser is frozen into the image at
# build time (see Dockerfile); nothing is downloaded at run time.
mcp.mount(
    create_proxy({
        "mcpServers": {
            "default": {
                # Use the pnpm-installed binary directly. Using `npx -y` here can fail
                # after a restart with ERR_MODULE_NOT_FOUND because npx's cache is
                # incomplete (transitive deps like language-subtag-registry's JSON files
                # don't always round-trip cleanly). The lockfile install at build time
                # baked the full dependency tree into the image, so the binary is reliable.
                "command": "/opt/camoufox-mcp/node_modules/.bin/camoufox-mcp-server",
                "args": [],
            }
        }
    }),
    namespace="cf",
)


async def _camoufox_health_watcher():
    """Exit the gateway process if the camoufox subprocess goes unreachable.

    Camoufox crashes on some pages (observed: nowsecure.nl triggers an
    unhandledRejection that kills the camoufox-mcp-server Node.js process).
    When that happens the FastMCP proxy's STDIO pipe is dead and subsequent
    cf_* tool calls fail with "Connection closed", with no auto-recovery.

    Solution: poll cf_camoufox_status every 30s. On 2 consecutive failures,
    os._exit(1). Docker's `restart: unless-stopped` policy on the gateway
    respawns the container, which respawns the camoufox subprocess via the
    eager-init below. Total recovery: ~10s of downtime per crash.
    """
    from fastmcp import Client
    consecutive_failures = 0
    while True:
        await asyncio.sleep(30)
        try:
            async with Client(mcp) as c:
                await c.call_tool("cf_camoufox_status", {})
            consecutive_failures = 0
        except Exception as e:
            consecutive_failures += 1
            print(
                f"[health] camoufox check failed ({consecutive_failures}/2): {e}",
                flush=True,
            )
            if consecutive_failures >= 2:
                print(
                    "[health] camoufox unreachable, exiting for docker auto-restart",
                    flush=True,
                )
                import os
                os._exit(1)


async def _init_and_run():
    """Eagerly populate proxy mounts so all child tools are registered before the
    HTTP server accepts connections.

    Without this, the camoufox-mcp subprocess is only spawned on the first
    tools/list call. Claude Code's MCP client may receive just the gateway's
    own tools without the 17 cf_* tools that come from the mounted proxy until
    a later list_changed notification.
    """
    from fastmcp import Client
    async with Client(mcp) as c:
        tools = await c.list_tools()
        print(f"[startup] {len(tools)} tools registered:", flush=True)
        for t in tools:
            print(f"  - {t.name}", flush=True)

    # Start the watchdog after eager-init confirms the proxy is up.
    asyncio.create_task(_camoufox_health_watcher())

    await mcp.run_async(transport="http", host="0.0.0.0", port=8000, path="/mcp")


if __name__ == "__main__":
    import asyncio
    asyncio.run(_init_and_run())

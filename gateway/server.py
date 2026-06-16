"""toolbox-gateway: FastMCP server aggregating homelab agentic tools.

Adding a new tool:
1. If it needs a new container, add it to ../docker-compose.yml on the same `toolbox` network.
2. If it's a pure Python dep, add it to requirements.txt.
3. Add an @mcp.tool() function below.
4. Rebuild + restart: `docker compose up -d --build gateway`
   Connected MCP clients receive tools/list_changed automatically and refresh.

Exposed at: http://<host>:8000/mcp (streamable HTTP transport)
"""

import json
import os
import subprocess
import tempfile
from pathlib import Path

import httpx
from fastmcp import FastMCP
from fastmcp.server import create_proxy
from markitdown import MarkItDown

SEARXNG_URL = os.environ.get("SEARXNG_URL", "http://searxng:8080")
CRAWL4AI_URL = os.environ.get("CRAWL4AI_URL", "http://crawl4ai:11235")
STIRLING_URL = os.environ.get("STIRLING_URL", "http://stirling-pdf:8080")
WHISPER_URL = os.environ.get("WHISPER_URL", "http://whisper:8000")

mcp = FastMCP(
    "toolbox",
    instructions=(
        "Homelab toolbox: search the web, fetch JS-rendered pages, convert "
        "documents to markdown, manipulate PDFs, extract video metadata and "
        "transcripts. Prefer these tools over training-knowledge guesses when "
        "the user needs fresh, specific, or fully-rendered content."
    ),
)

md_converter = MarkItDown()


@mcp.tool
def search_web(query: str, max_results: int = 10) -> list[dict]:
    """Search the web via SearXNG (federated meta-search, no rate limits).

    Args:
        query: search terms
        max_results: how many results to return (default 10, max 30)
    """
    r = httpx.get(
        f"{SEARXNG_URL}/search",
        params={"q": query, "format": "json"},
        timeout=20,
    )
    r.raise_for_status()
    results = r.json().get("results", [])
    return [
        {
            "title": x.get("title", ""),
            "url": x.get("url", ""),
            "snippet": x.get("content", "")[:300],
            "engine": x.get("engine", ""),
        }
        for x in results[: min(max_results, 30)]
    ]


@mcp.tool
def fetch_page(url: str, render_js: bool = True) -> str:
    """Fetch a web page and return clean markdown.

    Uses Crawl4AI (Playwright-backed). JS rendering on by default — handles
    SPAs, React/Next/Vue, Cloudflare-protected pages, login-gated dashboards.

    Args:
        url: fully-qualified URL
        render_js: if True (default), runs full browser. If False, plain HTTP fetch.
    """
    r = httpx.post(
        f"{CRAWL4AI_URL}/md",
        json={"url": url, "f": "fit"},  # fit = readability-style extraction
        timeout=90,
    )
    r.raise_for_status()
    body = r.json()
    return body.get("markdown", "") or body.get("html", "")


@mcp.tool
def convert_document(source: str) -> str:
    """Convert a document (PDF/DOCX/PPTX/XLSX/HTML/images-with-OCR) to markdown.

    Args:
        source: URL or local path. Local paths must be reachable inside the gateway container.
    """
    if source.startswith(("http://", "https://")):
        r = httpx.get(source, timeout=60, follow_redirects=True)
        r.raise_for_status()
        suffix = Path(source.split("?")[0]).suffix or ".bin"
        with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as f:
            f.write(r.content)
            path = f.name
        try:
            return md_converter.convert(path).text_content
        finally:
            os.unlink(path)
    return md_converter.convert(source).text_content


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
    stream — NOT YouTube's low-quality `format 18` fallback you get without a JS runtime. The
    stream is taken as-is (no re-encode), so audio stays pristine.

    Args:
        url: any yt-dlp-supported URL (1000+ sites).
        kind: "audio" (default) or "video".
        quality: "best" (default) or an explicit yt-dlp `-f` format selector to override.
        max_mb: refuse files larger than this (the bytes ride in the response). Raise for big video.

    Returns: {"filename", "ext", "format", "size_bytes", "content_b64"} — decode content_b64 to the file.
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
        pdf_bytes = httpx.get(source, timeout=60, follow_redirects=True).content
    else:
        pdf_bytes = Path(source).read_bytes()

    files = {"fileInput": ("doc.pdf", pdf_bytes, "application/pdf")}
    r = httpx.post(
        f"{STIRLING_URL}/api/v1/convert/pdf/text",
        files=files,
        data={"outputFormat": "txt"},
        timeout=120,
    )
    r.raise_for_status()
    return r.text


@mcp.tool
def ping() -> str:
    """Health check / hot-reload smoke test. Returns 'pong'. Use to verify the toolbox is reachable."""
    return "pong"


@mcp.tool
def transcribe_audio(
    source: str,
    model: str = "base.en",
    language: str | None = None,
) -> dict:
    """Transcribe audio/video to text via local Whisper (GPU-accelerated on AMD 5700 XT via Vulkan).

    Use for YouTube videos without auto-captions, podcasts, recorded meetings, or any audio file.
    For URLs, audio is downloaded via yt-dlp (1000+ sites supported) and normalized before transcription.

    Args:
        source: URL (yt-dlp supports YouTube/Vimeo/podcast feeds/etc) or local path.
        model: whisper model. English-only: tiny.en/base.en/small.en/medium.en (default base.en).
               Multilingual: tiny/base/small/medium/large-v3/large-v3-turbo. Bigger = more accurate,
               slower, larger first-download. base.en is ~80x realtime on the 5700 XT; large-v3 is
               ~8-12x realtime. For social-media-grade precision, use large-v3 or large-v3-turbo.
        language: optional ISO 639-1 code (en, es, etc). Auto-detect if omitted.

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
                # Fall back to direct HTTP for non-yt-dlp URLs
                r = httpx.get(source, timeout=120, follow_redirects=True)
                r.raise_for_status()
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
        r.raise_for_status()
        return r.json()


# Mount whit3rabbit/camoufox-mcp's 21 browser-automation tools into our surface.
# Camoufox is a privacy-focused Firefox fork with C++-level anti-fingerprinting,
# so this is what we use whenever Crawl4AI gets caught by bot-detection (Cloudflare,
# DataDome, etc) or we need true multi-step browser automation.
# Tools surface as `mcp__toolbox__cf_<name>`. First call downloads ~300MB Firefox binary.
mcp.mount(
    create_proxy({
        "mcpServers": {
            "default": {
                # Use the pnpm-installed binary directly. Using `npx -y` here can fail
                # after LXC restart with ERR_MODULE_NOT_FOUND because npx's cache is
                # incomplete (transitive deps like language-subtag-registry's JSON files
                # don't always round-trip cleanly). The pnpm global install at build time
                # baked the full dependency tree into the image, so the binary is reliable.
                "command": "camoufox-mcp-server",
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
    cf_* tool calls fail with "Connection closed" — no auto-recovery.

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
                    "[health] camoufox unreachable — exiting for docker auto-restart",
                    flush=True,
                )
                import os
                os._exit(1)


async def _init_and_run():
    """Eagerly populate proxy mounts so all child tools are registered before the
    HTTP server accepts connections.

    Without this, the camoufox-mcp subprocess is only spawned on the first
    tools/list call. Claude Code's MCP client may receive just the parent's
    direct tools (the 7 native @mcp.tool ones) without the 17 cf_* tools that
    come from the mounted proxy until a later list_changed notification.
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

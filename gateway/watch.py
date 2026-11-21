"""tb-watch: does one real job with every toolbox tool, on a schedule.

Why this exists: the toolbox used to be checked only for "is the process up".
On 2026-10-02 a rebuild silently pulled a newer browser and every browsing
call failed for seven weeks while the check stayed green. This watcher calls
each tool the way an agent does (over MCP, through the public endpoint) and
checks that the answer is right, not just that one came back.

What it does with the result:
- writes /health/status.json, which the gateway reads to tell agents, in the
  error message itself, that a tool is known broken, since when, and what to
  use instead (and to answer the toolbox_status tool).

A tool is marked DOWN after 2 failed checks in a row (one failure is retried
after 60 s, so a real break is reported within about a minute of being seen).
It is marked UP again after one good check.
"""

import asyncio
import datetime as dt
import json
import math
import os
import time
import traceback
from pathlib import Path

from fastmcp import Client

GATEWAY_URL = os.environ.get("GATEWAY_MCP_URL", "http://gateway:8000/mcp")
STATUS_FILE = Path(os.environ.get("STATUS_FILE", "/health/status.json"))
RETRY_AFTER_FAILURE_S = 60
RETRY_AFTER_BUSY_S = 60
FAILURES_TO_MARK_DOWN = 2
# The browser runs one page at a time, and an open multi-step session holds it.
# A browser call that failed only because it waited for the browser says one of
# these: the browser is busy, not broken. The gateway uses the same list to tell
# agents so.
BROWSER_BUSY_MARKERS = (
    "Timed out waiting for a browse slot",
    "Too many concurrent browse requests",
    "Too many active sessions",
)
PAGE_PROBE = "https://www.wikipedia.org/"
YOUTUBE_PROBE = "https://www.youtube.com/watch?v=jNQXAC9IVRw"  # "Me at the zoo", 19 s, first YouTube video
# A web page on your own network, read with fetch_page and with the browser. The
# default is the toolbox's own Stirling-PDF page: it draws itself with JavaScript,
# and its address is a private one, so both tools take the same path as for a
# homelab page an agent asks for. Set LAN_CHECK_URL (and LAN_CHECK_TEXT, words the
# page always shows) in .env to check a real address on your network instead.
LAN_PROBE = os.environ.get("LAN_CHECK_URL") or "http://stirling-pdf:8080/"
LAN_PROBE_TEXT = os.environ.get("LAN_CHECK_TEXT") or "No files yet"
# An everyday search. Good results name at least two of its three words; a broken
# engine that answers for one word only (docker.com) does not.
SEARCH_PROBE = "docker compose healthcheck"


def _text(result) -> str:
    """Everything a tool returned, as one string, for the content checks."""
    parts = []
    for c in getattr(result, "content", None) or []:
        t = getattr(c, "text", None)
        if t:
            parts.append(t)
    sc = getattr(result, "structured_content", None)
    if sc:
        parts.append(json.dumps(sc)[:20000])
    return "\n".join(parts)


def _data(result):
    sc = getattr(result, "structured_content", None) or {}
    return sc.get("result", sc) if isinstance(sc, dict) else sc


def expect_contains(*needles):
    def check(result):
        text = _text(result).lower()
        missing = [n for n in needles if n.lower() not in text]
        if missing:
            raise AssertionError(f"answer did not contain {missing!r}; got: {_text(result)[:300]!r}")
    return check


def expect_on_topic_results(query, at_least):
    """At least `at_least` results must be about the query: each names two thirds of its words."""
    words = query.lower().split()
    need = math.ceil(len(words) * 2 / 3)

    def on_topic(hit):
        hay = " ".join(str(hit.get(k, "")) for k in ("title", "snippet", "url")).lower()
        return sum(w in hay for w in words) >= need

    def check(result):
        data = _data(result)
        if not isinstance(data, list) or len(data) == 0:
            raise AssertionError(f"no search results; got: {_text(result)[:300]!r}")
        hits = [h for h in data if isinstance(h, dict)]
        good = [h for h in hits if on_topic(h)]
        if len(good) < at_least:
            off = [h.get("url", "")[:80] for h in hits if not on_topic(h)][:3]
            raise AssertionError(f"only {len(good)} of {len(hits)} results were about {query!r}; off topic: {off}")
    return check


def expect_audio(result):
    data = _data(result)
    if not isinstance(data, dict) or float(data.get("audio_seconds", 0)) <= 0.3 or len(data.get("audio_b64", "")) < 1000:
        raise AssertionError(f"no usable audio returned; got keys {list(data) if isinstance(data, dict) else type(data)}")


def expect_media(result):
    data = _data(result)
    if not isinstance(data, dict) or int(data.get("size_bytes", 0)) < 10_000:
        raise AssertionError(f"no media file returned; got: {str(data)[:300]!r}")


# name -> (tool, args, validator, interval seconds, timeout seconds)
# The gateway's own "ping" check doubles as "is the endpoint reachable at all".
CHECKS = {
    "ping": ("ping", {}, expect_contains("pong"), 60, 20),
    "search_web": ("search_web", {"query": SEARCH_PROBE, "max_results": 5}, expect_on_topic_results(SEARCH_PROBE, 3), 600, 40),
    # Not example.com: it asks not to be used for monitoring, and its text changed in 2026.
    "fetch_page": ("fetch_page", {"url": PAGE_PROBE}, expect_contains("Wikipedia"), 600, 120),
    # python.org loads a tracker that DNS filters (AdGuard Home, Pi-hole) commonly block,
    # so behind such a filter this also catches the browser guard failing whole pages
    # over a DNS-blocked sub-request. The page title, not just "Python": every answer
    # repeats the URL, so a challenge or error page would also contain "python".
    "cf_browse": ("cf_browse", {"url": "https://www.python.org/", "timeout": 60000}, expect_contains("Welcome to Python.org"), 600, 150),
    "convert_document": ("convert_document", {"source": "/app/fixtures/check.html"}, expect_contains("4K9TQ"), 1800, 60),
    "pdf_extract_text": ("pdf_extract_text", {"source": "/app/fixtures/check.pdf"}, expect_contains("7Q2Z"), 1800, 90),
    "transcribe_audio": ("transcribe_audio", {"source": "/app/fixtures/check.wav", "model": "base.en"}, expect_contains("health check"), 3600, 300),
    "synthesize_speech": ("synthesize_speech", {"text": "Health check.", "steps": 8}, expect_audio, 3600, 600),
    "extract_video": ("extract_video", {"url": YOUTUBE_PROBE, "want_transcript": False}, expect_contains("zoo"), 21600, 180),
    "download_media": ("download_media", {"url": YOUTUBE_PROBE, "kind": "audio", "max_mb": 5}, expect_media, 21600, 300),
    # The same two page readers on an address on your own network (see LAN_PROBE).
    "fetch_page_lan": ("fetch_page", {"url": LAN_PROBE}, expect_contains(LAN_PROBE_TEXT), 600, 120),
    "cf_browse_lan": ("cf_browse", {"url": LAN_PROBE, "timeout": 60000}, expect_contains(LAN_PROBE_TEXT), 600, 150),
}

# The checks of a tool on your own network, by the tool's main check.
LAN_CHECKS = {"fetch_page": "fetch_page_lan", "cf_browse": "cf_browse_lan"}

# Every tool the gateway exposes maps to the check that proves it works.
# All 17 browser tools share one browser, so cf_browse speaks for all of them.
COVERS = {name: name for name in CHECKS if name not in LAN_CHECKS.values()}
COVERS["cf_*"] = "cf_browse"

DESCRIPTIONS = {
    "ping": "the toolbox endpoint itself",
    "search_web": "web search",
    "fetch_page": "page fetching (Crawl4AI)",
    "cf_browse": "browser tools (Camoufox, all cf_* tools)",
    "convert_document": "document conversion",
    "pdf_extract_text": "PDF text extraction (Stirling)",
    "transcribe_audio": "transcription (Whisper)",
    "synthesize_speech": "text to speech",
    "extract_video": "video metadata (yt-dlp)",
    "download_media": "media download (yt-dlp)",
    "fetch_page_lan": "page fetching on your own network (Crawl4AI)",
    "cf_browse_lan": "browser tools on your own network (Camoufox, all cf_* tools)",
}


def now() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


def iso(t: dt.datetime | None) -> str | None:
    return t.isoformat(timespec="seconds") if t else None


class State:
    def __init__(self):
        self.tools: dict[str, dict] = {}
        self.started = now()
        self._load()

    def _load(self):
        """Keep status across restarts, so a restart does not forget a break."""
        try:
            old = json.loads(STATUS_FILE.read_text())
            for name, t in old.get("tools", {}).items():
                if name in CHECKS:
                    self.tools[name] = t
        except FileNotFoundError:
            pass
        except Exception as e:
            print(f"[watch] could not read old status ({e}); starting fresh", flush=True)
        for name, (_, _, _, interval, _) in CHECKS.items():
            t = self.tools.setdefault(name, {})
            t.setdefault("status", "unknown")
            t.pop("notified", None)
            t.setdefault("consecutive_failures", 0)
            t["interval_s"] = interval
            t["description"] = DESCRIPTIONS.get(name, name)

    def save(self):
        payload = {
            "updated": iso(now()),
            "watcher_started": iso(self.started),
            "covers": COVERS,
            "lan_checks": LAN_CHECKS,
            "tools": self.tools,
        }
        STATUS_FILE.parent.mkdir(parents=True, exist_ok=True)
        tmp = STATUS_FILE.with_suffix(".tmp")
        tmp.write_text(json.dumps(payload, indent=2))
        tmp.replace(STATUS_FILE)

    def record_busy(self, name: str):
        """The tool could not be tried because it was busy: neither a pass nor a failure."""
        t = self.tools[name]
        stamp = now()
        t["last_check"] = iso(stamp)
        t["last_busy"] = iso(stamp)
        self.save()

    def record(self, name: str, ok: bool, duration: float, error: str | None):
        t = self.tools[name]
        stamp = now()
        t["last_check"] = iso(stamp)
        t["duration_s"] = round(duration, 2)
        if ok:
            t["consecutive_failures"] = 0
            t["last_ok"] = iso(stamp)
            if t["status"] != "up":
                t["status"] = "up"
                t["since"] = iso(stamp)
        else:
            t["consecutive_failures"] += 1
            t["last_error"] = (error or "")[:600]
            t["last_error_at"] = iso(stamp)
            if t["consecutive_failures"] == 1:
                t["first_failure_at"] = iso(stamp)
            if t["consecutive_failures"] >= FAILURES_TO_MARK_DOWN and t["status"] != "down":
                t["status"] = "down"
                t["since"] = t.get("first_failure_at") or iso(stamp)
        self.save()


async def run_check(state: State, name: str):
    tool, args, validate, interval, timeout = CHECKS[name]
    # Give the gateway time to start, and stagger the first round so the checks
    # do not all hit the box at once.
    await asyncio.sleep(20 + list(CHECKS).index(name) * 5)
    while True:
        start = time.monotonic()
        ok, error = True, None
        try:
            async def call():
                async with Client(GATEWAY_URL, timeout=timeout) as c:
                    return await c.call_tool(tool, args, timeout=timeout)
            result = await asyncio.wait_for(call(), timeout=timeout + 15)
            validate(result)
        except Exception as e:
            ok = False
            # Drop the note the gateway adds for agents; keep the error itself.
            error = f"{type(e).__name__}: {e}".split("\n\n[toolbox]")[0].strip()
            if not str(e).strip():
                error = traceback.format_exc(limit=2)
        duration = time.monotonic() - start
        await asyncio.sleep(handle_result(state, name, ok, duration, error))


def handle_result(state: State, name: str, ok: bool, duration: float, error: str | None) -> float:
    """Record one check's outcome and return how many seconds to wait before the next."""
    interval = CHECKS[name][3]
    if not ok and any(m in (error or "") for m in BROWSER_BUSY_MARKERS):
        state.record_busy(name)
        print(f"[watch] {name}: busy in {duration:.1f}s (the browser was in use; not counted)", flush=True)
        return RETRY_AFTER_BUSY_S
    state.record(name, ok, duration, error)
    print(f"[watch] {name}: {'ok' if ok else 'FAIL'} in {duration:.1f}s" + ("" if ok else f" | {error[:200]}"), flush=True)
    failures = state.tools[name]["consecutive_failures"]
    if failures == 0:
        return interval
    if failures < FAILURES_TO_MARK_DOWN:
        return RETRY_AFTER_FAILURE_S  # confirm quickly
    return min(interval, 300)  # while broken, notice recovery quickly


async def main():
    state = State()
    state.save()
    print(f"[watch] watching {len(CHECKS)} checks via {GATEWAY_URL}", flush=True)
    await asyncio.gather(*(run_check(state, n) for n in CHECKS))


if __name__ == "__main__":
    asyncio.run(main())

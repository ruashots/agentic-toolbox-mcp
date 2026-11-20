"""See the toolbox work: a few real tool calls, and what came back.

It is part of the gateway image, so it runs the same from any shell:

    docker exec tb-gateway python demo.py

It takes under a minute. Set DEMO_SVG to also save the output as an image
(docs/media/demo.svg is made this way):

    docker exec -e DEMO_SVG=/app/demo.svg tb-gateway python demo.py
    docker cp tb-gateway:/app/demo.svg docs/media/demo.svg

(/app, not /tmp: /tmp lives in RAM in the container, and docker cp cannot reach it.)
"""

import asyncio
import json
import os
import time

from fastmcp import Client
from rich.console import Console
from rich.text import Text

URL = os.environ.get("TOOLBOX_URL", "http://localhost:8000/mcp")
WIDTH = 88
console = Console(record=True, width=WIDTH)


def data(result):
    """A tool's structured answer, or its text parsed as JSON when that is all there is."""
    sc = getattr(result, "structured_content", None)
    if isinstance(sc, dict):
        return sc.get("result", sc)
    text = "".join(getattr(c, "text", "") for c in result.content or [])
    try:
        return json.loads(text)
    except ValueError:
        return text


def row(tool, what, seconds):
    took = f"{seconds:5.1f} s"
    pad = max(1, WIDTH - 16 - len(what) - len(took))
    console.print(Text.assemble((f"{tool:<15} ", "bold cyan"), what, " " * pad, (took, "dim")))


def detail(text, style=""):
    console.print(Text(" " * 16 + text[: WIDTH - 16], style=style))


async def call(client, tool, args):
    start = time.monotonic()
    result = await client.call_tool(tool, args, raise_on_error=False)
    return result, time.monotonic() - start


def headings(markdown):
    return [line.lstrip("#").strip() for line in markdown.splitlines() if line.startswith("## ")]


async def main():
    async with Client(URL, timeout=180) as c:
        tools = [t.name for t in await c.list_tools()]
        browser = [t for t in tools if t.startswith("cf_")]
        console.print(Text.assemble(("Agentic Toolbox", "bold"), f"  {URL}"))
        console.print(f"{len(tools)} tools: {len(tools) - len(browser)} of its own, "
                      f"{len(browser)} cf_* tools for the stealth browser\n")

        r, s = await call(c, "search_web", {"query": "model context protocol", "max_results": 3})
        row("search_web", '"model context protocol"', s)
        if r.is_error:
            detail(str(data(r)), "red")
        for i, hit in enumerate([] if r.is_error else data(r), 1):
            site = hit["url"].split("/")[2].removeprefix("www.")
            detail(f"{i}. {hit['title'][:43]:<43}  {site}")

        page = "https://en.wikipedia.org/wiki/Large_language_model"
        r, s = await call(c, "fetch_page", {"url": page})
        row("fetch_page", page.removeprefix("https://"), s)
        md = data(r) if isinstance(data(r), str) and not r.is_error else ""
        sections = headings(md)
        detail(f"{len(md):,} characters of markdown, {len(sections)} sections:")
        detail(", ".join(sections[:4]) + ", ...", "italic")

        r, s = await call(c, "cf_browse", {"url": "https://www.python.org/", "timeout": 60000})
        row("cf_browse", "www.python.org, in the stealth browser", s)
        d = data(r)
        title = d.get("title") if isinstance(d, dict) else None
        detail(f'page title "{title}"' if title else str(d)[:70], "" if title else "red")

        video = "https://www.youtube.com/watch?v=jNQXAC9IVRw"
        r, s = await call(c, "extract_video", {"url": video, "want_transcript": False})
        row("extract_video", video.removeprefix("https://www."), s)
        d = data(r)
        if isinstance(d, dict):
            detail(f'"{d.get("title")}", uploaded by {d.get("uploader")}, {d.get("duration_seconds")} s long')
        else:
            detail(str(d)[:70], "red")

        r, s = await call(c, "toolbox_status", {})
        row("toolbox_status", "what the watcher saw", s)
        checks = data(r).get("tools", {}) if isinstance(data(r), dict) else {}
        up = [n for n, t in checks.items() if t.get("status") == "up"]
        detail(f"{len(up)} of {len(checks)} scheduled checks passing")
        for name, t in checks.items():
            if t.get("status") != "up":
                detail(f"{name}: {t.get('status')}", "yellow")

    svg = os.environ.get("DEMO_SVG")
    if svg:
        console.save_svg(svg, title="docker exec tb-gateway python demo.py")


asyncio.run(main())

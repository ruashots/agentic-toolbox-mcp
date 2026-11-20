"""Tool behavior that can be checked without the backing services."""
import asyncio
from pathlib import Path

import pytest
from fastmcp import Client
from fastmcp.exceptions import ToolError
from PIL import Image, ImageDraw

import server

FIXTURES = Path(__file__).resolve().parent.parent / "gateway" / "fixtures"


def call(tool, args):
    async def run():
        async with Client(server.mcp) as c:
            return await c.call_tool(tool, args)
    return asyncio.run(run())


def test_convert_document_reads_a_document():
    result = call("convert_document", {"source": str(FIXTURES / "check.html")})
    assert "4K9TQ" in result.content[0].text


def test_convert_document_says_so_when_an_image_has_no_readable_text(tmp_path):
    image = tmp_path / "invoice.png"
    canvas = Image.new("RGB", (600, 120), "white")
    ImageDraw.Draw(canvas).text((10, 40), "INVOICE 48213 TOTAL 99.50", fill="black")
    canvas.save(image)
    with pytest.raises(ToolError, match="No text found"):
        call("convert_document", {"source": str(image)})


def test_convert_document_does_not_transcribe_audio():
    # markitdown transcribes audio with Google's speech service when SpeechRecognition
    # is installed. It is kept out of the image, so audio must not come back as text.
    with pytest.raises(ToolError, match="use transcribe_audio"):
        call("convert_document", {"source": str(FIXTURES / "check.wav")})


def test_backend_errors_carry_the_services_own_reason():
    import httpx
    assert server._backend_error(httpx.Response(500, json={"detail": "boom"})) == "500: boom"
    assert "corrupted" in server._backend_error(
        httpx.Response(400, json={"status": 400, "message": "PDF file appears to be corrupted"}))
    assert server._backend_error(httpx.Response(502, text="Bad gateway")) == "502: Bad gateway"
    assert server._backend_error(httpx.Response(503, text="")) == "503: Service Unavailable"


def test_crawl4ai_errors_are_cut_down_to_what_happened():
    import httpx
    detail = ("500: Unexpected error in _crawl_web at line 778 in _crawl_web (crawler.py):\n"
              "Error: Failed on navigating ACS-GOTO:\n"
              "Page.goto: net::ERR_HTTP_RESPONSE_CODE_FAILURE at https://example.com/x\n"
              "Call log:\n  - navigating to \"https://example.com/x\"\n\n\nCode context:\n 773   tag=\"GOTO\",")
    r = httpx.Response(500, json={"detail": detail})
    assert server._crawl_error(r) == "net::ERR_HTTP_RESPONSE_CODE_FAILURE at https://example.com/x"
    r = httpx.Response(500, json={"detail": "Blocked by anti-bot protection: Structural: minimal_text on small page"})
    assert server._crawl_error(r).startswith("Blocked by anti-bot protection: Structural")

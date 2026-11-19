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

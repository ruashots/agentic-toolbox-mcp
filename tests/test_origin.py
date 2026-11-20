"""The MCP endpoint refuses requests made by web pages.

A browser adds an Origin header to the requests a page makes; MCP clients such as
Claude Code send none. The app is built exactly as the gateway serves it.
"""
import asyncio

import fastmcp
import httpx

import server

INITIALIZE = {
    "jsonrpc": "2.0", "id": 1, "method": "initialize",
    "params": {"protocolVersion": "2025-06-18", "capabilities": {},
               "clientInfo": {"name": "test", "version": "1"}},
}
HEADERS = {"Content-Type": "application/json", "Accept": "application/json, text/event-stream"}
# The gateway as another machine on the network sees it.
LAN_URL = "http://192.168.1.5:8000"


def initialize(extra_headers):
    app = server.mcp.http_app(path="/mcp", **getattr(server, "http_guard", dict)())

    async def run():
        async with app.lifespan(app):
            transport = httpx.ASGITransport(app=app)
            async with httpx.AsyncClient(transport=transport, base_url=LAN_URL) as client:
                return await client.post("/mcp", json=INITIALIZE, headers={**HEADERS, **extra_headers})

    return asyncio.run(run())


def test_an_mcp_client_without_an_origin_header_connects():
    response = initialize({})
    assert response.status_code == 200
    assert response.headers.get("mcp-session-id")


def test_a_page_from_another_site_is_refused():
    response = initialize({"Origin": "http://attacker.example:8000"})
    assert response.status_code == 403
    assert not response.headers.get("mcp-session-id")


def test_a_page_that_rebinds_its_name_to_this_machine_is_refused():
    # DNS rebinding: the page's own name now points at the toolbox, so Host and
    # Origin agree with each other. Both are still the attacker's name.
    response = initialize({"Host": "rebind.attacker.example:8000",
                           "Origin": "http://rebind.attacker.example:8000"})
    assert response.status_code == 403


def test_an_origin_listed_by_the_operator_connects(monkeypatch):
    monkeypatch.setattr(fastmcp.settings, "http_allowed_origins", ["http://localhost:6274"])
    response = initialize({"Origin": "http://localhost:6274"})
    assert response.status_code == 200

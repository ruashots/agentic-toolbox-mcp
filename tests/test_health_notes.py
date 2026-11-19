"""What an agent is told when a tool call fails, built from the status file the
watcher writes. The status is produced by watch.State itself, so these tests
also hold the watcher and the gateway to the same file format."""
import asyncio
import datetime as dt
import json
import socket

import pytest
from fastmcp import Client
from fastmcp.exceptions import ToolError

import server
import watch


@pytest.fixture
def state(tmp_path, monkeypatch):
    path = tmp_path / "status.json"
    monkeypatch.setattr(watch, "STATUS_FILE", path)
    monkeypatch.setattr(server, "STATUS_FILE", path)
    s = watch.State()
    s.save()  # what the watcher does at startup, before the first check
    return s


def fail(state, name, times):
    for _ in range(times):
        state.record(name, ok=False, duration=1.0, error="boom")


def test_no_status_file_means_no_health_data(tmp_path, monkeypatch):
    monkeypatch.setattr(server, "STATUS_FILE", tmp_path / "missing.json")
    note = server._health_note("fetch_page")
    assert "No health data available" in note
    assert server._ALTERNATIVES["fetch_page"] in note


def test_a_tool_that_is_down_is_a_known_outage(state):
    fail(state, "search_web", 2)
    note = server._health_note("search_web")
    assert "KNOWN OUTAGE" in note
    assert "Retrying will not help" in note
    assert server._ALTERNATIVES["search_web"] in note
    assert "notified" not in note  # nobody is notified; the note must not say so


def test_a_tool_that_failed_once_may_be_breaking(state):
    state.record("fetch_page", ok=True, duration=1.0, error=None)
    fail(state, "fetch_page", 1)
    note = server._health_note("fetch_page")
    assert "failed its most recent scheduled check" in note
    assert "passed" not in note


def test_a_tool_that_passed_its_last_check_gets_one_retry(state):
    state.record("convert_document", ok=True, duration=1.0, error=None)
    note = server._health_note("convert_document")
    assert "passed its last scheduled real-job check" in note
    assert "One retry is reasonable" in note


def test_a_tool_never_checked_does_not_claim_it_passed(state):
    note = server._health_note("transcribe_audio")
    assert "has not been checked yet" in note
    assert "passed" not in note


def test_every_browser_tool_reads_the_cf_browse_check(state):
    fail(state, "cf_browse", 2)
    assert "KNOWN OUTAGE" in server._health_note("cf_browse_screenshot")
    assert "KNOWN OUTAGE" in server._health_note("cf_browse_session_start")


def test_old_health_data_is_called_stale(state, monkeypatch):
    payload = json.loads(server.STATUS_FILE.read_text())
    old = dt.datetime.now(dt.timezone.utc) - dt.timedelta(hours=1)
    payload["updated"] = old.isoformat(timespec="seconds")
    server.STATUS_FILE.write_text(json.dumps(payload))
    assert "Health data is stale" in server._health_note("ping")


def test_toolbox_status_lists_errors_and_alternatives_for_broken_tools(state):
    state.record("ping", ok=True, duration=0.1, error=None)
    fail(state, "download_media", 2)

    async def call():
        async with Client(server.mcp) as c:
            return await c.call_tool("toolbox_status", {})

    data = asyncio.run(call()).data
    assert data["watcher"] == "ok"
    assert data["tools"]["ping"]["status"] == "up"
    assert "last_error" not in data["tools"]["ping"]
    broken = data["tools"]["download_media"]
    assert broken["status"] == "down"
    assert broken["last_error"] == "boom"
    assert broken["use_instead"] == server._ALTERNATIVES["download_media"]


def test_a_failing_call_carries_the_health_note(state, monkeypatch):
    # A port nothing listens on: the call fails with a connection error.
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    monkeypatch.setattr(server, "SEARXNG_URL", f"http://127.0.0.1:{port}")
    fail(state, "search_web", 2)

    async def call():
        async with Client(server.mcp) as c:
            await c.call_tool("search_web", {"query": "anything"})

    with pytest.raises(ToolError) as err:
        asyncio.run(call())
    message = str(err.value)
    assert "The service behind this tool is not reachable." in message
    assert "[toolbox] KNOWN OUTAGE" in message

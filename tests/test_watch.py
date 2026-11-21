"""The watcher's promise: a tool is DOWN after 2 failed checks in a row, UP again
after one good check, and the status survives a watcher restart."""
import json

import pytest

import watch


@pytest.fixture
def status_file(tmp_path, monkeypatch):
    path = tmp_path / "status.json"
    monkeypatch.setattr(watch, "STATUS_FILE", path)
    return path


def test_new_state_starts_every_check_as_unknown(status_file):
    state = watch.State()
    assert set(state.tools) == set(watch.CHECKS)
    for name, t in state.tools.items():
        assert t["status"] == "unknown"
        assert t["consecutive_failures"] == 0
        assert t["interval_s"] == watch.CHECKS[name][3]
        assert t["description"] == watch.DESCRIPTIONS[name]


def test_one_failure_does_not_mark_a_tool_down(status_file):
    state = watch.State()
    state.record("search_web", ok=True, duration=1.0, error=None)
    state.record("search_web", ok=False, duration=1.0, error="engines refused")
    t = state.tools["search_web"]
    assert t["status"] == "up"
    assert t["consecutive_failures"] == 1
    assert t["last_error"] == "engines refused"
    assert t["first_failure_at"] == t["last_check"]


def test_two_failures_in_a_row_mark_it_down_since_the_first(status_file):
    state = watch.State()
    state.record("fetch_page", ok=False, duration=1.0, error="timeout")
    first = state.tools["fetch_page"]["first_failure_at"]
    state.record("fetch_page", ok=False, duration=1.0, error="timeout")
    t = state.tools["fetch_page"]
    assert t["status"] == "down"
    assert t["since"] == first


def test_one_good_check_marks_it_up_again(status_file):
    state = watch.State()
    for _ in range(3):
        state.record("cf_browse", ok=False, duration=1.0, error="browser crashed")
    assert state.tools["cf_browse"]["status"] == "down"
    state.record("cf_browse", ok=True, duration=1.0, error=None)
    t = state.tools["cf_browse"]
    assert t["status"] == "up"
    assert t["consecutive_failures"] == 0
    assert t["since"] == t["last_ok"] == t["last_check"]


def test_status_survives_a_watcher_restart(status_file):
    state = watch.State()
    state.record("ping", ok=False, duration=0.1, error="refused")
    state.record("ping", ok=False, duration=0.1, error="refused")
    since = state.tools["ping"]["since"]
    reloaded = watch.State()
    assert reloaded.tools["ping"]["status"] == "down"
    assert reloaded.tools["ping"]["since"] == since


def test_every_record_is_written_for_the_gateway(status_file):
    state = watch.State()
    state.record("ping", ok=True, duration=0.1, error=None)
    payload = json.loads(status_file.read_text())
    assert payload["tools"]["ping"]["status"] == "up"
    assert payload["covers"]["cf_*"] == "cf_browse"
    assert payload["updated"]


def test_long_errors_are_cut_to_600_characters(status_file):
    state = watch.State()
    state.record("ping", ok=False, duration=0.1, error="x" * 5000)
    assert len(state.tools["ping"]["last_error"]) == 600


class _Result:
    """Just enough of an MCP tool result for the content checks."""
    def __init__(self, text):
        from mcp.types import TextContent
        self.content = [TextContent(type="text", text=text)]
        self.structured_content = None


def test_the_browser_check_needs_the_real_page_not_just_its_url():
    check = watch.CHECKS["cf_browse"][2]
    check(_Result('{"url": "https://www.python.org/", "title": "Welcome to Python.org"}'))
    for page in ('{"url": "https://www.python.org/", "title": "Just a moment..."}',
                 '{"url": "https://www.python.org/", "title": "404 Not Found"}'):
        with pytest.raises(AssertionError):
            check(_Result(page))


def test_a_check_without_a_description_does_not_crash_the_watcher(status_file, monkeypatch):
    checks = dict(watch.CHECKS, my_new_tool=("my_new_tool", {}, watch.expect_contains("ok"), 600, 30))
    monkeypatch.setattr(watch, "CHECKS", checks)
    state = watch.State()
    assert state.tools["my_new_tool"]["description"] == "my_new_tool"


BUSY_ERRORS = (
    "ToolError: Timed out waiting for a browse slot.",
    "ToolError: Too many concurrent browse requests. Try again later.",
    "ToolError: Too many active sessions. Maximum is 1.",
)


def test_a_busy_browser_is_neither_a_pass_nor_a_failure(status_file):
    state = watch.State()
    watch.handle_result(state, "cf_browse", True, 3.0, None)
    for error in BUSY_ERRORS:
        wait = watch.handle_result(state, "cf_browse", False, 30.1, error)
        assert wait == watch.RETRY_AFTER_BUSY_S
    t = state.tools["cf_browse"]
    assert t["status"] == "up"
    assert t["consecutive_failures"] == 0
    assert t["last_busy"]


def test_a_real_browser_failure_still_marks_it_down(status_file):
    state = watch.State()
    watch.handle_result(state, "cf_browse", False, 5.0, "ToolError: Connection closed")
    watch.handle_result(state, "cf_browse", False, 5.0, "ToolError: Connection closed")
    assert state.tools["cf_browse"]["status"] == "down"


class _Search:
    """A search_web result as the watcher receives it: the hits as structured content."""
    def __init__(self, hits):
        self.content = []
        self.structured_content = {"result": hits}


def _hit(title, url, snippet=""):
    return {"title": title, "url": url, "snippet": snippet, "engine": "test"}


ON_TOPIC = [
    _hit("Docker Compose health checks: a guide", "https://last9.io/blog/docker-compose-health-checks/"),
    _hit("How to add a healthcheck in Docker Compose", "https://www.warp.dev/terminus/docker-compose-health-check"),
    _hit("Services", "https://docs.docker.com/reference/compose-file/services/", "healthcheck declares a check"),
]
ONE_WORD = [  # what a broken engine returns: pages that match one word of the search
    _hit("Docker: Accelerated Container Application Development", "https://www.docker.com/"),
    _hit("Docker Desktop", "https://www.docker.com/products/docker-desktop/"),
    _hit("Install Docker Desktop on Windows", "https://docs.docker.com/desktop/setup/install/windows-install/"),
]


def test_the_search_check_passes_when_most_results_are_on_topic():
    check = watch.CHECKS["search_web"][2]
    check(_Search(ON_TOPIC + ONE_WORD[:2]))


def test_the_search_check_fails_when_results_match_one_word_only():
    check = watch.CHECKS["search_web"][2]
    with pytest.raises(AssertionError, match="only 2 of 5 results"):
        check(_Search(ON_TOPIC[:2] + ONE_WORD))


def test_the_search_check_fails_on_no_results():
    with pytest.raises(AssertionError, match="no search results"):
        watch.CHECKS["search_web"][2](_Search([]))


def test_the_network_checks_use_the_same_tools_on_one_page_on_your_network():
    assert watch.CHECKS["fetch_page_lan"][0] == "fetch_page"
    assert watch.CHECKS["cf_browse_lan"][0] == "cf_browse"
    assert watch.CHECKS["fetch_page_lan"][1]["url"] == watch.CHECKS["cf_browse_lan"][1]["url"] == watch.LAN_PROBE
    assert watch.LAN_CHECKS == {"fetch_page": "fetch_page_lan", "cf_browse": "cf_browse_lan"}


def test_the_network_page_can_be_set_in_the_environment(monkeypatch):
    import importlib
    monkeypatch.setenv("LAN_CHECK_URL", "http://192.168.1.10:8085/")
    monkeypatch.setenv("LAN_CHECK_TEXT", "My dashboard")
    try:
        importlib.reload(watch)
        assert watch.CHECKS["fetch_page_lan"][1]["url"] == "http://192.168.1.10:8085/"
        watch.CHECKS["fetch_page_lan"][2](_Result("Welcome to My dashboard"))
        with pytest.raises(AssertionError):
            watch.CHECKS["cf_browse_lan"][2](_Result('{"url": "http://192.168.1.10:8085/", "title": "502 Bad Gateway"}'))
    finally:
        monkeypatch.delenv("LAN_CHECK_URL")
        monkeypatch.delenv("LAN_CHECK_TEXT")
        importlib.reload(watch)
    assert watch.LAN_PROBE == "http://stirling-pdf:8080/"


def test_covers_maps_tools_and_the_network_checks_are_listed_apart(status_file):
    state = watch.State()
    state.save()
    payload = json.loads(status_file.read_text())
    assert "fetch_page_lan" not in payload["covers"]
    assert payload["covers"]["fetch_page"] == "fetch_page"
    assert payload["lan_checks"] == watch.LAN_CHECKS

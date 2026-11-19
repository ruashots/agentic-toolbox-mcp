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

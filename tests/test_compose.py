"""Promises that live in docker-compose.yml, checked on the file itself."""
from pathlib import Path

import pytest
import yaml

COMPOSE = yaml.safe_load((Path(__file__).resolve().parent.parent / "docker-compose.yml").read_text())
SERVICES = COMPOSE["services"]


def _core_limits(service):
    core = (service.get("ulimits") or {}).get("core")
    if isinstance(core, dict):
        return core.get("soft"), core.get("hard")
    return core, core


@pytest.mark.parametrize("name", sorted(SERVICES))
def test_no_service_can_leave_a_core_dump(name):
    # A limit of 0 is not enough: on WSL 2 and on hosts with a crash collector, the
    # kernel hands the crash to that collector, which ignores 0 and saves the dump.
    # A limit of 1 byte makes the kernel drop the dump itself.
    assert _core_limits(SERVICES[name]) == (1, 1)

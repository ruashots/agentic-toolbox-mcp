"""What the build and CI fetch is pinned to exact, checkable content."""
import re
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent


def test_ci_actions_are_pinned_to_a_commit():
    # A tag such as @v4 can be moved to other code; a commit cannot.
    workflow = yaml.safe_load((ROOT / ".github" / "workflows" / "ci.yml").read_text())
    uses = [step["uses"] for job in workflow["jobs"].values()
            for step in job["steps"] if "uses" in step]
    assert uses
    for action in uses:
        assert re.fullmatch(r"[\w.-]+/[\w.-]+@[0-9a-f]{40}", action), action

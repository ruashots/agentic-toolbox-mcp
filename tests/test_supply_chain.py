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


LOCKS = ["gateway/requirements.lock", "whisper/requirements.lock", "tts/requirements.lock",
         "tts/requirements-torch.lock", "tests/requirements.txt"]


def _requirements(path):
    """(pin, hashes) for each requirement in a pip requirements file."""
    entries = []
    for line in (ROOT / path).read_text().splitlines():
        line = line.split("#", 1)[0].strip() if not line.lstrip().startswith("--hash") else line.strip()
        if not line:
            continue
        if line.startswith("--hash=sha256:"):
            entries[-1][1].append(line.split(":", 1)[1].rstrip(" \\"))
        else:
            entries.append((line.rstrip(" \\"), []))
    return entries


def test_every_python_package_is_pinned_with_its_sha256():
    for path in LOCKS:
        entries = _requirements(path)
        assert entries, path
        for pin, hashes in entries:
            assert re.fullmatch(r"[A-Za-z0-9._-]+==\S+", pin), (path, pin)
            assert hashes and all(re.fullmatch(r"[0-9a-f]{64}", h) for h in hashes), (path, pin)


def test_the_builds_refuse_a_file_whose_hash_is_not_listed():
    for dockerfile in ("gateway/Dockerfile", "whisper/Dockerfile", "tts/Dockerfile"):
        text = (ROOT / dockerfile).read_text().replace("\\\n", " ")
        commands = [c for line in text.splitlines() for c in line.split("&&")
                    if "pip install" in c and "-r requirements" in c]
        assert commands, dockerfile
        for command in commands:
            assert "--require-hashes" in command, (dockerfile, command)
    # torch comes only from the PyTorch index, everything else only from PyPI
    assert "--extra-index-url" not in (ROOT / "tts" / "Dockerfile").read_text()

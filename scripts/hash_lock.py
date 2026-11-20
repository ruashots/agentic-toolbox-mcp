"""Write the sha256 of every published file next to each pin in a requirements.lock.

    python scripts/hash_lock.py gateway/requirements.lock [more.lock ...]

Each line must be name==version. The hashes come from PyPI's JSON API, or, for a
version with a local tag such as torch==2.13.0+cpu, from the PyTorch CPU index.
With hashes in the file, pip installs only files whose sha256 is listed, whatever
index serves them. Run it again after changing a version. Standard library only.
"""
import json
import re
import sys
import urllib.request
from html.parser import HTMLParser

PYTORCH_INDEX = "https://download.pytorch.org/whl/cpu"


def fetch(url):
    with urllib.request.urlopen(url, timeout=60) as response:
        return response.read().decode()


class _Links(HTMLParser):
    def __init__(self):
        super().__init__()
        self.links = []

    def handle_starttag(self, tag, attrs):
        if tag == "a":
            self.links.extend(v for k, v in attrs if k == "href")


def hashes(name, version):
    if "+" in version:  # a local version tag: the PyTorch CPU index
        parser = _Links()
        parser.feed(fetch(f"{PYTORCH_INDEX}/{name.lower()}/"))
        wanted = f"-{version.replace('+', '%2B')}-"
        found = {link.split("#sha256=")[1] for link in parser.links
                 if "#sha256=" in link and wanted in link.split("#")[0]}
    else:
        data = json.loads(fetch(f"https://pypi.org/pypi/{name}/{version}/json"))
        found = {f["digests"]["sha256"] for f in data["urls"]}
    if not found:
        sys.exit(f"hash_lock: no files found for {name}=={version}")
    return sorted(found)


def main(paths):
    for path in paths:
        out = []
        for line in open(path).read().splitlines():
            stripped = line.strip()
            if not stripped or stripped.startswith(("#", "--hash")):
                if not stripped.startswith("--hash"):
                    out.append(line)
                continue
            pin = re.fullmatch(r"([A-Za-z0-9._-]+)==([^\s\\]+)(\s*\\)?", stripped)
            if not pin:
                sys.exit(f"hash_lock: {path}: not a name==version line: {line}")
            name, version = pin.group(1), pin.group(2)
            out.append(f"{name}=={version} \\")
            found = hashes(name, version)
            out.extend(f"    --hash=sha256:{h}" + (" \\" if i < len(found) - 1 else "")
                       for i, h in enumerate(found))
        open(path, "w").write("\n".join(out) + "\n")
        print(f"{path}: done")


if __name__ == "__main__":
    main(sys.argv[1:])

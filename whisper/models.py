"""Whisper model files: where they come from, and the check that they are the right ones.

Each model is downloaded from one fixed commit of Hugging Face's ggerganov/whisper.cpp
repository, the first time it is used, and kept only if its sha256 matches the one
below. To move to newer files on purpose, take the commit and the files' sha256 from
the repository's page (Files and versions) and change both here.
Standard library only.
"""
import hashlib
import os
import urllib.request
from pathlib import Path

REPO = "https://huggingface.co/ggerganov/whisper.cpp/resolve"
REVISION = "5359861c739e955e79d9a303bcbc70fb988958b1"  # main as of 2024-10-29, unchanged since

# name -> (file, sha256)
MODELS = {
    "tiny": ("ggml-tiny.bin", "be07e048e1e599ad46341c8d2a135645097a538221678b7acdd1b1919c6e1b21"),
    "tiny.en": ("ggml-tiny.en.bin", "921e4cf8686fdd993dcd081a5da5b6c365bfde1162e72b08d75ac75289920b1f"),
    "base": ("ggml-base.bin", "60ed5bc3dd14eea856493d334349b405782ddcaf0028d4b5df4088345fba2efe"),
    "base.en": ("ggml-base.en.bin", "a03779c86df3323075f5e796cb2ce5029f00ec8869eee3fdfb897afe36c6d002"),
    "small": ("ggml-small.bin", "1be3a9b2063867b937e64e2ec7483364a79917e157fa98c5d94b5c1fffea987b"),
    "small.en": ("ggml-small.en.bin", "c6138d6d58ecc8322097e0f987c32f1be8bb0a18532a3f88f734d1bbf9c41e5d"),
    "medium": ("ggml-medium.bin", "6c14d5adee5f86394037b4e4e8b59f1673b6cee10e3cf0b11bbdbee79c156208"),
    "medium.en": ("ggml-medium.en.bin", "cc37e93478338ec7700281a7ac30a10128929eb8f427dda2e865faa8f6da4356"),
    "large-v3": ("ggml-large-v3.bin", "64d182b440b98d5203c4f9bd541544d84c605196c4f7b845dfa11fb23594d1e2"),
    "large-v3-turbo": ("ggml-large-v3-turbo.bin", "1fc70f774d38eb169993ac391eea357ef47c88757ef72ee5943879b7e8e2bc69"),
}


class ChecksumMismatch(Exception):
    """The download is not the pinned file. It has been deleted."""


def url(name: str) -> str:
    return f"{REPO}/{REVISION}/{MODELS[name][0]}"


def download(name: str, dest: Path) -> None:
    """Download model `name` to `dest`, or raise. `dest` only ever holds a checked file."""
    expected = MODELS[name][1]
    tmp = dest.with_name(dest.name + ".tmp")
    digest = hashlib.sha256()
    try:
        with urllib.request.urlopen(url(name), timeout=60) as response, open(tmp, "wb") as out:
            while chunk := response.read(1 << 20):
                digest.update(chunk)
                out.write(chunk)
        if digest.hexdigest() != expected:
            raise ChecksumMismatch(
                f"the downloaded {name} model has sha256 {digest.hexdigest()}, not the pinned "
                f"{expected}; it was deleted")
        os.replace(tmp, dest)
    finally:
        tmp.unlink(missing_ok=True)
